"""One revision-bound investigation and release service for HTTP and MCP."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from .checkpoints import CheckpointError, CheckpointStore, evidence_signature
from .evidence import assess_lineage, compare_records, inspect_record, load_fixture
from .graph import GraphStore, RevisionConflict
from .optimizer import solve
from .planning import build_plan, public_plan
from .policies import separation_policy, policy_description, validate_separation_policy
from .review import candidate_review, compare_record_identity, release_review, record_profile
from .memory import make_memories, evaluate, procedure_matches
from .organization import OrganizationBrain


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_NAMES = ("train.jsonl", "eval.jsonl", "repair.json")
SCOPE = (
    "Draft train/eval pools; pairwise conflicts in this captured candidate graph; "
    "predeclared additive exclusion costs. Conservative export covers confirmed "
    "and unresolved candidates. It does not certify absence of undiscovered leakage "
    "or repair an already-trained model."
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _catalog_digest(fixture: dict) -> str:
    return _sha(_json({key: value for key, value in fixture.items() if not key.startswith("_")}).encode("utf-8"))


def _verify_evidence(fixture: dict, candidates: list[dict]) -> None:
    original = {c["id"]: c for c in fixture["candidates"]}
    if len(candidates) != len(original) or {c["id"] for c in candidates} != set(original):
        raise ValueError("Receipt candidate inventory differs from the original fixture.")
    for candidate in candidates:
        captured = original[candidate["id"]]
        static = lambda c: {key: value for key, value in c.items() if key not in {"status", "evidence"}}
        if static(candidate) != static(captured):
            raise ValueError("A candidate's endpoints or original hypothesis changed.")
        assessment = candidate.get("evidence")
        if assessment is None:
            if candidate["status"] != captured["status"]:
                raise ValueError("An evidence verdict changed without an inspection receipt.")
            continue
        attempts = assessment.get("attempts", [])
        if not attempts or assessment.get("verdict") != candidate["status"]:
            raise ValueError("Candidate status is not backed by its inspection receipt.")
        for attempt in attempts:
            method = attempt.get("method")
            if separation_policy(fixture) == "exact_record" and method != "record_identity":
                raise ValueError("Receipt method is not permitted by the exact-record policy.")
            if method == "record_identity":
                replayed = compare_record_identity(fixture, candidate["train_id"], candidate["eval_id"])
            elif method in {"exact", "normalized_ast"}:
                replayed = compare_records(fixture, candidate["train_id"], candidate["eval_id"], method)
            elif method == "provenance":
                paths = attempt.get("graph_provenance", {})
                replayed = assess_lineage(fixture, candidate["train_id"], candidate["eval_id"], paths.get("train", {}), paths.get("eval", {}))
            else:
                raise ValueError("Receipt contains an unsupported investigation method.")
            if any(replayed.get(key) != attempt.get(key) for key in ("verdict", "method", "reason", "evidence")):
                raise ValueError("Inspection receipt does not replay against the original captured evidence.")
        if any(assessment.get(key) != attempts[-1].get(key) for key in ("verdict", "method", "reason", "evidence")):
            raise ValueError("Final verdict does not match the final recorded inspection.")


def _fingerprints(fixture: dict) -> dict[str, str]:
    root = Path(fixture["_root"]).resolve()
    names = {r["content_path"] for r in fixture["records"]}
    names.update(e["path"] for e in fixture["entities"] if e.get("path"))
    names.update(e["evidence_path"] for e in fixture["links"] if e.get("evidence_path"))
    result = {}
    for name in sorted(names):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Captured artifact is missing or escapes the fixture root.")
        result[name] = _sha(path.read_bytes())
    return result


def verify_release(directory: str | Path, fixture: dict | None = None) -> dict:
    """Verify an exported bundle against its mathematical and byte-level receipts.

    This is a consistency check, not a digital signature. Passing the original
    fixture also checks the bundle against independently retained source bytes.
    """
    directory = Path(directory).resolve()
    paths = {name: directory / name for name in ARTIFACT_NAMES}
    for path in paths.values():
        if not path.resolve().is_relative_to(directory) or not path.is_file():
            raise ValueError("Release is incomplete or contains an escaping file link.")
    report = json.loads(paths["repair.json"].read_text())
    if report.get("schema_version") not in {1, 2}:
        raise ValueError("Unsupported repair receipt schema.")
    if report.get("conservative") is not True:
        raise ValueError("A release receipt must conservatively cover unresolved candidates.")
    if report["solution"].get("hypothetical"):
        raise ValueError("A counterfactual plan cannot be exported as an actual release.")
    if report["solution"].get("revision") != report["revision"]:
        raise ValueError("The repair certificate belongs to a different evidence revision.")
    if report["schema_version"] == 2 and ("review" not in report or "separation_policy" not in report):
        raise ValueError("Version 2 receipts require their policy and release review.")
    checks = {}
    selected_policy = validate_separation_policy(report.get("separation_policy", "answer_leakage"))
    if fixture is not None and selected_policy != separation_policy(fixture):
        raise ValueError("Release separation policy differs from the captured catalog.")
    records = report["input_records"]
    by_id = {r["id"]: r for r in records}
    if len(by_id) != len(records):
        raise ValueError("Duplicate source record IDs in receipt.")
    kept = set(report["solution"]["kept"])
    excluded = set(report["solution"]["excluded"])
    if kept & excluded or kept | excluded != set(by_id):
        raise ValueError("Repair is not a complete disjoint partition.")
    checks["complete_partition"] = True
    if any(by_id[i].get("protected", False) for i in excluded):
        raise ValueError("Repair excludes a protected record.")
    checks["protected_records_retained"] = True
    if sum(by_id[i]["cost"] for i in excluded) != report["solution"]["cost"]:
        raise ValueError("Exclusion costs do not match the repair.")
    checks["exclusion_cost_matches"] = True
    statuses = {"confirmed", "unresolved"} if report["conservative"] else {"confirmed"}
    conflicts = [c for c in report["candidates"] if c["status"] in statuses]
    if any(c["train_id"] not in excluded and c["eval_id"] not in excluded for c in conflicts):
        raise ValueError("An active conflict survives the repair.")
    checks["active_conflicts_covered"] = True
    seen = set()
    for split in ("train", "eval"):
        name = split + ".jsonl"
        raw = paths[name].read_bytes()
        if _sha(raw) != report["artifact_sha256"][name]:
            raise ValueError(f"{name} was modified after export.")
        expected = {i for i in kept if by_id[i]["split"] == split}
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
        ids = [row["id"] for row in rows]
        if len(ids) != len(set(ids)) or set(ids) != expected:
            raise ValueError(f"{name} does not contain exactly the retained IDs.")
        for row in rows:
            source = by_id[row["id"]]
            if row["split"] != split or {key: value for key, value in row.items() if key != "content"} != source:
                raise ValueError("Manifest metadata differs from source receipt.")
            if _sha(row["content"].encode("utf-8")) != source["content_sha256"]:
                raise ValueError("Exported content differs from captured source bytes.")
            seen.add(row["id"])
    checks["manifest_hashes_match"] = True
    checks["retained_content_unchanged"] = True
    checks["retained_ids_exact"] = seen == kept
    if fixture is not None:
        if fixture["id"] != report["fixture_id"] or _fingerprints(fixture) != report["input_fingerprints"]:
            raise ValueError("Source evidence changed after release capture.")
        if _catalog_digest(fixture) != report.get("input_catalog_sha256"):
            raise ValueError("Original candidate catalog or record policy changed.")
        original = {r["id"]: r for r in fixture["records"]}
        if set(original) != set(by_id) or any(original[i] != by_id[i] for i in original):
            raise ValueError("Receipt record metadata differs from the supplied fixture.")
        checks["original_fixture_matches"] = True
        _verify_evidence(fixture, report["candidates"])
        checks["evidence_verdicts_replayed"] = True
    # The optimizer's exported validator independently checks capacities,
    # conservation, residual reachability, and equality of cut cost and flow.
    from .optimizer import validate_solution
    certificate_checks = validate_solution(records, report["candidates"], report["solution"], conservative=report["conservative"])
    checks["optimality_certificate_valid"] = True
    review = release_review(records, report["candidates"], report["solution"], selected_policy)
    if "review" in report and report["review"] != review:
        raise ValueError("Release review counts, warnings or reasons disagree with the certified partition.")
    if "review" in report:
        checks["release_review_matches"] = True
    return {"verified": all(checks.values()), "checks": checks, "review": review, "separation_policy": selected_policy,
            "verification_level": "original-evidence-and-certificate" if fixture is not None else "bundle-consistency-and-certificate",
            "certificate_checks": certificate_checks, "cost": report["solution"]["cost"],
            "excluded": sorted(excluded), "kept": sorted(kept),
            "revision": report["revision"], "conservative": report["conservative"], "scope": report["scope"]}


class HoldoutService:
    def __init__(self, fixture_path: str | Path | None = None, runtime_dir: str | Path | None = None, url: str | None = None,
                 checkpoint_store: CheckpointStore | None = None, resume_id: str | None = None):
        self.lock = threading.RLock()
        self.fixture_path = fixture_path
        self.fixture = load_fixture(fixture_path)
        self.catalog_path = Path(fixture_path) if fixture_path else PROJECT_ROOT / "fixtures" / "demo" / "dataset.json"
        if self.catalog_path.is_dir():
            self.catalog_path = self.catalog_path / "dataset.json"
        self.catalog_sha256 = _sha(self.catalog_path.read_bytes())
        self.run_id = resume_id or uuid4().hex[:12]
        self.checkpoint_store = checkpoint_store
        self._restoring = False
        self.source_watch_guard = None  # bound by persistent local API; never trust stale watched bytes
        self.runtime_dir = Path(runtime_dir) if runtime_dir else PROJECT_ROOT / ".runtime" / "sessions" / self.run_id
        self.store = GraphStore(self.runtime_dir / "engine", url=url)
        try:
            self.store.seed(self.fixture)
            self.store.initialize_tasks()
            self.engine = self.store.capabilities()
            if not self.engine["native_maxflow"]:
                raise RuntimeError("Native algo.maxFlow is unavailable. Run the documented native setup; no fallback solver is used for HOLDOUT releases.")
        except BaseException:
            self.store.close(delete_graph=True)
            raise
        org_audit_root = (checkpoint_store.workspace / 'organization' if checkpoint_store
                          else self.runtime_dir / 'organization')
        self.organization = OrganizationBrain(
            self.store, PROJECT_ROOT / 'fixtures' / 'organization' / 'organization.json',
            org_audit_root / f'{self.run_id}.json', self.run_id)
        self.input_fingerprints = _fingerprints(self.fixture)
        self._cache: dict[tuple[int, bool], dict] = {}
        self._plan_cache: dict | None = None
        self._memory_cache: dict | None = None
        self._attempts: dict[str, set[str]] = {}
        self._review_cache: dict[str, dict] = {}
        self.release: dict | None = None
        self.release_dir: Path | None = None
        self.agent = {"status": "idle", "mode": None, "steps": 0, "message": "Ready to investigate."}
        self._event("seed_release", f"Captured {len(self.fixture['records'])} records and {len(self.fixture['candidates'])} candidate conflicts in FalkorDB.")
        if self.checkpoint_store:
            if resume_id:
                try:
                    self.restore_checkpoint(self.checkpoint_store.load(resume_id))
                except BaseException:
                    # Never leave a partially restored graph reachable.
                    self.store.close(delete_graph=True)
                    raise
            else:
                self._save_checkpoint()

    def _save_checkpoint(self) -> None:
        if self.checkpoint_store and not self._restoring:
            self.checkpoint_store.save(
                run_id=self.run_id, catalog_path=self.catalog_path,
                catalog_digest=_catalog_digest(self.fixture),
                fingerprints=self.input_fingerprints,
                candidates=self.store.candidates(), release_staged=self.release is not None,
            )

    def restore_checkpoint(self, data: dict) -> None:
        """Re-run recorded evidence methods; never trust a saved verdict alone."""
        with self.lock:
            if data.get("run_id") != self.run_id or data.get("catalog_path") != str(self.catalog_path.resolve()):
                raise CheckpointError("Recovery run or source path mismatch.")
            if data.get("catalog_digest") != _catalog_digest(self.fixture) or data.get("fingerprints") != self.input_fingerprints:
                raise CheckpointError("Captured input changed; refusing to replay old evidence.")
            decisions = data.get("decisions", [])
            if len(decisions) > len(self.fixture["candidates"]):
                raise CheckpointError("Recovery contains more decisions than candidates.")
            original_ids = {c["id"] for c in self.fixture["candidates"]}
            seen = set()
            self._restoring = True
            try:
                for decision in decisions:
                    cid = decision.get("id")
                    methods = decision.get("methods")
                    if cid not in original_ids or cid in seen or not isinstance(methods, list) or not methods:
                        raise CheckpointError("Invalid or repeated candidate in recovery.")
                    seen.add(cid)
                    if any(m not in {"record_identity", "exact", "normalized_ast", "provenance"} for m in methods):
                        raise CheckpointError("Invalid recovery method.")
                    # Replay the stored sequence, not a new version of the auto policy.
                    replayed = self.investigate(cid, "auto", _replay_methods=methods)
                    receipt = replayed.get("evidence") or {}
                    digest = evidence_signature(receipt)
                    if (replayed["status"] != decision.get("verdict") or
                        [a.get("method") for a in receipt.get("attempts", [])] != methods or
                        digest != decision.get("evidence_sha256")):
                        raise CheckpointError("Recorded evidence does not replay identically; refusing recovery.")
                if data.get("release_staged"):
                    self.stage_release()
                recovered_release = self.release is not None and self.release.get("verified") is True
                self.agent.update(status="completed" if recovered_release else "interrupted", mode=None, steps=0,
                                  message=("Restored and independently verified the staged conservative release." if recovered_release else
                                           "Restored evidence from a durable checkpoint. Start the agent to continue."))
                self._event("session_resumed", f"Replayed {len(decisions)} evidence decisions from a checkpoint; previous model transcript was not trusted.")
            finally:
                self._restoring = False
            # Task leases are never trusted across restart. Reconstruct only
            # from replayed verdicts and attempted evidence methods.
            self.store.reconcile_tasks(self._task_decisions())
            self._save_checkpoint()

    def _event(self, tool: str, summary: str, **extra: Any) -> None:
        self.store.add_event({"timestamp": datetime.now(timezone.utc).isoformat(), "tool": tool, "summary": summary, **extra})

    def _unchanged(self) -> None:
        if not self._restoring and self.source_watch_guard:
            self.source_watch_guard(self.run_id)
        if _fingerprints(self.fixture) != self.input_fingerprints or _sha(self.catalog_path.read_bytes()) != self.catalog_sha256:
            raise ValueError("Captured evidence changed on disk. Start a new run to ingest a new snapshot.")

    @property
    def provider_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("HOLDOUT_MODEL"))

    def _solve(self, conservative: bool) -> dict:
        key = (self.store.revision(), conservative)
        if key not in self._cache:
            self._cache[key] = solve(self.store, conservative=conservative)
        return deepcopy(self._cache[key])

    def bounds(self) -> dict:
        with self.lock:
            lower, upper = self._solve(False), self._solve(True)
            gap = upper["cost"] - lower["cost"] if lower["feasible"] and upper["feasible"] else None
            return {"lower": lower, "upper": upper, "gap": gap}

    def review_candidate(self, candidate: dict) -> dict:
        """Cached byte-bound observations; attempts/verdict remain revision-local."""
        cid = candidate["id"]
        if cid not in self._review_cache:
            self._review_cache[cid] = candidate_review(self.fixture, candidate)
        review = deepcopy(self._review_cache[cid])
        tried = self._attempts.get(cid, set())
        for name, item in review["methods"].items():
            item["attempted"] = name in tried
            item["state"] = "unavailable" if not item["available"] else "exhausted" if name in tried else "available"
        review["pending_methods"] = [name for name, item in review["methods"].items() if item["state"] == "available"] if candidate["status"] == "unresolved" else []
        review["policy_verdict"] = candidate["status"]
        review["release_implication"] = (
            "Conservative export must exclude at least one endpoint while this candidate is unresolved." if candidate["status"] == "unresolved" else
            "At least one endpoint must be excluded under the selected policy." if candidate["status"] == "confirmed" else
            "This candidate adds no conflict constraint under the selected policy; no global independence is certified.")
        return review

    def candidate_summaries(self) -> list[dict]:
        with self.lock:
            result = []
            for c in self.store.candidates():
                review = self.review_candidate(c)
                result.append({**c, "method_applicability": review["methods"],
                               "pending_methods": review["pending_methods"],
                               "content_identical": review["identity"]["identical"]})
            return result

    def plan_investigation(self, expected_revision: int | None = None,
                           max_candidates: int | None = None, *, refresh: bool = False) -> dict:
        """Bounded native scenarios, stored separately from evidence and repair."""
        with self.lock:
            self._unchanged()
            revision = self.store.revision()
            if expected_revision is not None and expected_revision != revision:
                raise RevisionConflict("Cannot plan a stale evidence revision.")
            cap = int(os.environ.get("HOLDOUT_PLAN_MAX_CANDIDATES", "32")) if max_candidates is None else max_candidates
            seconds = float(os.environ.get("HOLDOUT_PLAN_MAX_SECONDS", "30"))
            cached = self._plan_cache
            if (not refresh and cached and cached["binding"]["revision"] == revision
                    and cached["coverage"]["max_candidates"] == cap):
                return public_plan(cached)
            candidates = self.store.candidates()
            reviews = {c["id"]: self.review_candidate(c) for c in candidates if c["status"] == "unresolved"}
            plan = build_plan(self.store, reviews, self.bounds(), run_id=self.run_id,
                              catalog_digest=_catalog_digest(self.fixture), policy=separation_policy(self.fixture),
                              max_candidates=cap, max_seconds=seconds)
            self._unchanged()
            self._plan_cache = plan
            next_id = (plan["next_candidate"] or {}).get("id")
            self._event("plan_investigation",
                        f"Scored {plan['coverage']['evaluated']} claims using {plan['work']['scenario_solves']} native scenarios; "
                        + (f"next: {next_id}." if next_id else plan["stop"]["reason"]),
                        result={"plan_id": plan["plan_id"], "revision": revision, "gap": plan["bounds"]["gap"],
                                "next_candidate": next_id, "stop_code": plan["stop"]["code"],
                                "evaluated": plan["coverage"]["evaluated"], "hypothetical": True})
            return public_plan(plan)

    def planning_report(self) -> dict:
        with self.lock:
            self._unchanged()
            if not self._plan_cache or self._plan_cache["binding"]["revision"] != self.store.revision():
                raise ValueError("No current plan exists. Calculate a plan for this evidence revision first.")
            return deepcopy(self._plan_cache)

    def _record_memories(self, candidate: dict) -> None:
        """Only replayable, deterministic evidence can become durable memory."""
        if not (candidate.get('evidence') or {}).get('attempts'):
            return
        profiles = {'train': record_profile(self.fixture, candidate['train_id']),
                    'eval': record_profile(self.fixture, candidate['eval_id'])}
        memories = make_memories(candidate, catalog_digest=_catalog_digest(self.fixture),
                                 policy=separation_policy(self.fixture),
                                 revision=self.store.revision(), profiles=profiles)
        self.store.upsert_memories(memories)
        self._memory_cache = None

    def _validate_memory(self, mem: dict, candidates: dict[str, dict], catalog_sha: str,
                         policy: str) -> dict:
        candidate = candidates.get(mem.get('candidate_id'))
        expected = None
        if candidate and (candidate.get('evidence') or {}).get('attempts'):
            profiles = {'train': record_profile(self.fixture, candidate['train_id']),
                        'eval': record_profile(self.fixture, candidate['eval_id'])}
            for item in make_memories(candidate, catalog_digest=catalog_sha, policy=policy,
                                      revision=mem.get('revision_observed', -1), profiles=profiles):
                if item['kind'] == mem.get('kind'):
                    expected = item
        return evaluate(mem, candidate, catalog_digest=catalog_sha, policy=policy,
                        expected=expected if expected else {'id': '__missing_expected_receipt__'})

    def recall_memories(self, candidate_id: str | None = None, kind: str | None = None,
                        limit: int = 60, include_stale: bool = False) -> dict:
        """Read graph memory with receipt and input validation. No evidence mutation.

        Historical procedures may *suggest* applicable tests for similar claims;
        they may never transfer verdicts. Unchanged evidence is checked before
        memory lookup, including all captured source fingerprints.
        """
        with self.lock:
            if kind is not None and kind not in {'episodic', 'semantic', 'procedural'}:
                raise ValueError('Unknown memory kind.')
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ValueError('Memory limit must be 1–500.')
            self._unchanged()
            if candidate_id is not None and candidate_id not in {c['id'] for c in self.store.candidates()}:
                raise KeyError(candidate_id)
            current = {c['id']: c for c in self.store.candidates()}
            raw = self.store.memories(candidate_id, kind, limit=limit)
            items = [self._validate_memory(m, current, _catalog_digest(self.fixture),
                                           separation_policy(self.fixture)) for m in raw]
            selected = [m for m in items if include_stale or m['validity'] == 'current']
            return {'run_id': self.run_id, 'revision': self.store.revision(),
                    'scope': 'Only current, replayable investigation receipts are authoritative; memory is not independent evidence.',
                    'count': len(selected), 'stale_suppressed': len(items) - len(selected),
                    'items': deepcopy(selected)}

    def memory_overview(self) -> dict:
        with self.lock:
            rev = self.store.revision()
            if self._memory_cache is not None and self._memory_cache.get('_revision') == rev:
                return {key: deepcopy(value) for key,value in self._memory_cache.items() if key != '_revision'}
            current = {c['id']: c for c in self.store.candidates()}
            records = self.store.memories(limit=500)
            counts = {'episodic': 0, 'semantic': 0, 'procedural': 0, 'stale': 0}
            binding_digest = _catalog_digest(self.fixture)
            policy = separation_policy(self.fixture)
            for item in records:
                checked = self._validate_memory(item, current, binding_digest, policy)
                if checked['validity'] == 'current':
                    counts[item['kind']] += 1
                else:
                    counts['stale'] += 1
            result = {'counts': counts, 'current_total': sum(counts[k] for k in ('episodic','semantic','procedural')),
                      'scope': 'Graph memories derived only from checked evidence, not free-form model assertions.'}
            self._memory_cache = {**result, '_revision': rev}
            return deepcopy(result)

    def suggest_procedures(self, candidate_id: str, limit: int = 5) -> dict:
        """Find valid, structurally compatible previous method sequences.

        Suggestions do not modify evidence or cause new methods to be executed.
        """
        with self.lock:
            target = self.candidate(candidate_id)
            profile = self.review_candidate(target)
            pending = profile['pending_methods']
            memories = self.recall_memories(kind='procedural',limit=500)['items']
            matches=[]
            target_shape={side:{'content_role': None,
                                'answer_symbols': bool(profile['record_profiles'][side]['answer_symbols'])}
                          for side in ('train','eval')}
            for mem in memories:
                if mem['candidate_id'] == candidate_id or not procedure_matches(mem, {'pending_methods': pending},separation_policy(self.fixture)):
                    continue
                shape = mem['content'].get('source_shape',{})
                # Only compare supported answer functions; content_role is not
                # currently a stable property of record_profile.
                if any(bool(shape.get(side,{}).get('answer_symbols')) != target_shape[side]['answer_symbols']
                       for side in ('train','eval')):
                    continue
                suggested = [m for m in mem['content']['method_sequence'] if m in pending]
                if suggested:
                    matches.append({'source_memory_id': mem['id'], 'source_candidate_id': mem['candidate_id'],
                                    'suggested_methods': suggested,
                                    'note': 'Procedure hint only; re-run evidence checks and derive a fresh verdict.'})
            return {'candidate_id': candidate_id, 'pending_methods': pending,
                    'suggestions': matches[:max(0,min(limit,20))],
                    'scope': 'Past methods are hints. No verdict is inherited and no current evidence is modified.'}

    def _planning_summary(self) -> dict | None:
        if self._plan_cache and self._plan_cache["binding"]["revision"] == self.store.revision():
            return public_plan(self._plan_cache, top=8)
        return None

    def next_candidate(self, bounds: dict | None = None) -> dict | None:
        with self.lock:
            bounds = bounds or self.bounds()
            by_id = {r["id"]: r for r in self.store.records()}
            lower_cover = set(bounds["lower"].get("excluded", []))
            options = []
            for c in self.store.candidates():
                if c["status"] != "unresolved" or not self.review_candidate(c)["pending_methods"]:
                    continue
                endpoints = [by_id[c["train_id"]], by_id[c["eval_id"]]]
                finite = [r["cost"] for r in endpoints if not r.get("protected")]
                covered = bool({c["train_id"], c["eval_id"]} & lower_cover)
                priority = 0 if covered else min(finite, default=sum(r["cost"] for r in by_id.values()) + 1)
                options.append({"id": c["id"], "impact": priority, "method": "auto", "priority_kind": "endpoint-cost heuristic", "already_covered_by_lower": covered})
            return max(options, key=lambda item: (item["impact"], item["id"]), default=None)

    def state(self, include_details: bool = False) -> dict:
        with self.lock:
            bounds = self.bounds()
            state = {"project": "HOLDOUT", "run_id": self.run_id,
                     "title": self.fixture.get("title", "Draft release investigation"),
                     "description": self.fixture.get("description", SCOPE), "mode": "live",
                     "revision": self.store.revision(),
                     "engine": {**self.engine, "name": "FalkorDB", "version": self.engine.get("module_version_text")},
                     "records": self.store.records(), "candidates": self.store.candidates(),
                     "events": self.store.events(), "bounds": bounds,
                     "next_candidate": self.next_candidate(bounds), "release": deepcopy(self.release),
                     "agent": deepcopy(self.agent), "provider_available": self.provider_available,
                     "import_summary": deepcopy(self.fixture.get("import_summary")),
                     "separation_policy": policy_description(self.fixture), "planning": self._planning_summary(),
                     "memory": self.memory_overview(), "coordination": self.coordination_status(),
                     "organization": self.organization.overview(self.release, self.store.records(), self.store.candidates())}
            if bounds["upper"].get("feasible"):
                state["repair_review"] = release_review(state["records"], state["candidates"], bounds["upper"], separation_policy(self.fixture))
            if include_details:
                state["details"] = {c["id"]: self.candidate_detail(c["id"]) for c in state["candidates"]}
            return state

    def compact_status(self) -> dict:
        with self.lock:
            bounds = self.bounds()
            candidates = self.store.candidates()
            return {"run_id": self.run_id, "revision": self.store.revision(),
                    "counts": {status: sum(c["status"] == status for c in candidates) for status in ("confirmed", "rejected", "unresolved")},
                    "bounds": {name: {k: bounds[name].get(k) for k in ("feasible", "cost", "excluded")} for name in ("lower", "upper")},
                    "gap": bounds["gap"], "next_candidate": self.next_candidate(bounds),
                    "release": self.release, "scope": SCOPE,
                    "planning": self._planning_summary(),
                    "planner_hint": "Call plan_investigation for graph-derived priorities. next_candidate is the legacy endpoint-cost baseline, not counterfactual analysis.",
                    "separation_policy": policy_description(self.fixture),
                    "memory": self.memory_overview()}

    def candidate(self, candidate_id: str) -> dict:
        for candidate in self.store.candidates():
            if candidate["id"] == candidate_id:
                return candidate
        raise KeyError(f"Unknown candidate: {candidate_id}")

    def inspect(self, record_id: str) -> dict:
        with self.lock:
            self._unchanged()
            result = inspect_record(self.fixture, record_id)
            result["answer_profile"] = record_profile(self.fixture, record_id)
            result["separation_policy"] = policy_description(self.fixture)
            profile = result["answer_profile"]
            result["available_methods"] = ["record_identity"]
            if separation_policy(self.fixture) == "answer_leakage":
                if profile["answer_symbols"]:
                    result["available_methods"].append("exact")
                if profile["normalized_symbols"]:
                    result["available_methods"].append("normalized_ast")
                result["available_methods"].append("provenance")
            self._event("inspect_record", f"Inspected captured source for {record_id}.", record_id=record_id)
            return result

    def trace(self, record_id: str, max_depth: int = 8) -> dict:
        with self.lock:
            if record_id not in {r["id"] for r in self.store.records()}:
                raise KeyError(record_id)
            result = self.store.provenance(record_id, max_depth)
            self._event("trace_provenance", f"FalkorDB returned {len(result['paths'])} bounded provenance paths for {record_id}.", record_id=record_id)
            return result

    def candidate_detail(self, candidate_id: str) -> dict:
        with self.lock:
            self._unchanged()
            c = self.candidate(candidate_id)
            return {"candidate": c, "review": self.review_candidate(c), "train": inspect_record(self.fixture, c["train_id"]),
                    "eval": inspect_record(self.fixture, c["eval_id"]),
                    "lineage": {"train": self.store.provenance(c["train_id"]), "eval": self.store.provenance(c["eval_id"])},
                    "assessment": c.get("evidence")}

    def _task_decisions(self) -> dict[str,dict]:
        choices = {}
        for c in self.store.candidates():
            review = self.review_candidate(c)
            remaining = review['pending_methods']
            code_methods = {'exact','normalized_ast','record_identity'}
            choices[c['id']] = {
                'done': c['status'] != 'unresolved' or not remaining,
                'role': 'code' if any(m in code_methods for m in remaining) else 'provenance'
            }
        return choices

    def coordination_status(self) -> dict:
        with self.lock:
            tasks = self.store.tasks()
            return {'run_id':self.run_id,'tasks':tasks,
                    'counts':{state:sum(t['state']==state for t in tasks)
                              for state in ('pending','leased','done')},
                    'scope':'One local release; task claims and handoffs are graph-backed. '
                            'A restart reconstructs tasks from evidence, not active leases.'}

    def claim_investigation(self, role: str, owner: str, lease_seconds: int = 60) -> dict:
        with self.lock:
            self._unchanged()
            claim = self.store.claim_task(role,owner,lease_seconds)
            if claim:
                self._event('task_claimed',f"{owner} claimed {claim['candidate_id']} as {role}.",
                            candidate_id=claim['candidate_id'], worker=owner)
            return {'claim':claim}

    def finish_investigation(self, candidate_id: str, lease_token: str,
                             *, handoff_role: str | None = None) -> dict:
        with self.lock:
            self._unchanged()
            self.store.require_claim(candidate_id,lease_token)
            choice=self._task_decisions()[candidate_id]
            if handoff_role:
                if (choice['done'] or handoff_role != choice['role'] or
                        self.store.task(candidate_id)['role'] == handoff_role):
                    raise ValueError('Handoff must target the specialist required by remaining evidence.')
            elif not choice['done']:
                raise ValueError('Candidate has remaining applicable evidence; hand off instead of marking done.')
            outcome=self.store.transition_task(candidate_id,lease_token,handoff_role=handoff_role)
            self._event('task_handoff' if handoff_role else 'task_completed',
                        f"{candidate_id} {'handed off to ' + handoff_role if handoff_role else 'finished'}.",
                        candidate_id=candidate_id, result=outcome)
            return outcome

    def investigate(self, candidate_id: str, method: str = "auto", expected_revision: int | None = None,
                    *, _replay_methods: list[str] | None = None,
                    lease_token: str | None = None) -> dict:
        allowed = {"auto", "record_identity", "exact", "normalized_ast", "provenance"}
        if method not in allowed:
            raise ValueError("Unsupported investigation method.")
        with self.lock:
            self._unchanged()
            if expected_revision is not None and self.store.revision() != expected_revision:
                raise RevisionConflict("Evidence changed; reload the current revision.")
            if not self._restoring:
                self.store.require_claim(candidate_id, lease_token)
            c = self.candidate(candidate_id)
            if c["status"] != "unresolved":
                return c
            if _replay_methods is not None:
                if not self._restoring:
                    raise ValueError("Recorded method replay is only available during checkpoint restoration.")
                methods = list(_replay_methods)
            elif method == "auto":
                methods = self.review_candidate(c)["pending_methods"]
            else:
                info = self.review_candidate(c)["methods"][method]
                if not info["available"]:
                    raise ValueError("Method is unavailable: " + info["reason"])
                if method in self._attempts.get(candidate_id, set()):
                    return c  # Repeating an exhausted method cannot change immutable evidence.
                methods = [method]
            if not methods:
                return c
            attempts = deepcopy((c.get("evidence") or {}).get("attempts", []))
            for selected in methods:
                if separation_policy(self.fixture) == "exact_record" and selected != "record_identity":
                    raise ValueError("The exact-record policy uses only record_identity.")
                if selected == "record_identity":
                    assessment = compare_record_identity(self.fixture, c["train_id"], c["eval_id"])
                elif selected == "provenance":
                    t = self.store.provenance(c["train_id"])
                    e = self.store.provenance(c["eval_id"])
                    assessment = assess_lineage(self.fixture, c["train_id"], c["eval_id"], t, e)
                    assessment["graph_provenance"] = {"train": t, "eval": e}
                else:
                    assessment = compare_records(self.fixture, c["train_id"], c["eval_id"], selected)
                attempts.append(assessment)
                self._attempts.setdefault(candidate_id, set()).add(selected)
                self._event("investigate_candidate", f"{candidate_id} · {selected}: {assessment['verdict']}.", candidate_id=candidate_id, result=assessment)
                if assessment["verdict"] != "unresolved":
                    break
            if method == "auto":
                self._attempts.setdefault(candidate_id, set()).add("auto")
            result = {**attempts[-1], "attempts": attempts}
            if not self._restoring:
                # Guard commit as well as start, so slow evidence operations
                # cannot write after their lease expired or was superseded.
                self.store.require_claim(candidate_id, lease_token)
            self.store.set_candidate(candidate_id, result["verdict"], result, expected_revision=self.store.revision())
            self._record_memories(self.candidate(candidate_id))
            self._cache.clear()
            self._plan_cache = None
            self.release = None
            self.release_dir = None
            if lease_token is None and not self._restoring:
                # Manual and single-agent actions have no task ownership; sync
                # task state to the new authoritative evidence verdict.
                self.store.reconcile_tasks(self._task_decisions(), candidate_id=candidate_id)
            self._save_checkpoint()
            return self.candidate(candidate_id)

    def organization_context(self) -> dict:
        with self.lock:
            self._unchanged()
            return self.organization.overview(self.release, self.store.records(), self.store.candidates())

    def organization_policy(self, policy_id: str) -> dict:
        with self.lock:
            self._unchanged()
            self.organization.select_policy(policy_id)
            self._event('organization_policy', f'Local synthetic review policy selected: {policy_id}.')
            return self.organization_context()

    def organization_review(self, actor_id: str, decision: str) -> dict:
        with self.lock:
            self._unchanged()
            outcome = self.organization.review(actor_id, decision, self.release,
                                               self.store.records(), self.store.candidates())
            self._event('organization_review', f'Synthetic reviewer {actor_id} recorded {decision} for the current release.')
            return outcome

    def solve_release(self, conservative: bool = True) -> dict:
        with self.lock:
            result = self._solve(conservative)
            self._event("solve_release", f"Native FalkorDB max-flow produced {'a cost-' + str(result['cost']) + ' repair' if result['feasible'] else 'an infeasibility result'}.", conservative=conservative,
                        result={k: result.get(k) for k in ("feasible", "cost", "excluded", "revision")})
            return result

    def stage_release(self, conservative: bool = True, expected_revision: int | None = None) -> dict:
        with self.lock:
            self._unchanged()
            revision = self.store.revision()
            if expected_revision is not None and revision != expected_revision:
                raise RevisionConflict("Cannot export a stale evidence revision.")
            # A release is always conservative. The confirmed-only solve remains
            # useful as a lower bound, but cannot be mislabeled a safe release.
            if conservative is not True:
                raise ValueError("Release export must cover unresolved candidates. Use solve_release(false) to inspect the lower bound.")
            solution = self._solve(True)
            if not solution["feasible"]:
                raise ValueError("Required records make the conservative repair infeasible; inspect protected conflicts.")
            if self.release and self.release["revision"] == revision and self.release_dir:
                verify_release(self.release_dir, self.fixture)
                return deepcopy(self.release)
            output = self.runtime_dir / "releases" / f"r{revision}-{uuid4().hex[:8]}"
            output.mkdir(parents=True, exist_ok=False)
            records = self.store.records()
            kept = set(solution["kept"])
            hashes = {}
            for split in ("train", "eval"):
                rows = []
                for record in records:
                    if record["id"] in kept and record["split"] == split:
                        path = Path(self.fixture["_root"]) / record["content_path"]
                        rows.append({**record, "content": path.read_bytes().decode("utf-8")})
                data = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")
                name = split + ".jsonl"
                (output / name).write_bytes(data)
                hashes[name] = _sha(data)
            report = {"schema_version": 2, "project": "HOLDOUT", "run_id": self.run_id,
                      "fixture_id": self.fixture["id"], "revision": revision,
                      "created_at": datetime.now(timezone.utc).isoformat(), "conservative": True,
                      "scope": SCOPE, "engine": self.engine, "input_records": records,
                      "input_catalog_sha256": _catalog_digest(self.fixture),
                      "input_fingerprints": self.input_fingerprints, "candidates": self.store.candidates(),
                      "solution": solution, "artifact_sha256": hashes,
                      "separation_policy": separation_policy(self.fixture),
                      "review": release_review(records, self.store.candidates(), solution, separation_policy(self.fixture))}
            (output / "repair.json").write_text(_json(report))
            verification = verify_release(output, self.fixture)
            self.release_dir = output
            self.release = {**verification, "files": list(ARTIFACT_NAMES)}
            self._event("stage_release", f"Wrote and verified {len(kept)} retained records in train/eval manifests; exclusion cost {solution['cost']}.", result=self.release)
            self._save_checkpoint()
            return deepcopy(self.release)

    def artifact(self, name: str) -> str:
        with self.lock:
            if name not in ARTIFACT_NAMES:
                raise KeyError(name)
            if not self.release_dir or not self.release:
                raise ValueError("No verified release exists for the current revision.")
            self._unchanged()
            verify_release(self.release_dir, self.fixture)
            return (self.release_dir / name).read_text()

    def close(self) -> None:
        with self.lock:
            self.store.close(delete_graph=False)
