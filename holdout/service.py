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

from .evidence import assess_lineage, compare_records, inspect_record, load_fixture
from .graph import GraphStore, RevisionConflict
from .optimizer import solve


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
            if method in {"exact", "normalized_ast"}:
                replayed = compare_records(fixture, candidate["train_id"], candidate["eval_id"], method)
            elif method == "provenance":
                paths = attempt.get("graph_provenance", {})
                replayed = assess_lineage(fixture, candidate["train_id"], candidate["eval_id"], paths.get("train", {}), paths.get("eval", {}))
            else:
                raise ValueError("Receipt contains an unsupported investigation method.")
            if any(replayed.get(key) != attempt.get(key) for key in ("verdict", "method", "evidence")):
                raise ValueError("Inspection receipt does not replay against the original captured evidence.")
        if any(assessment.get(key) != attempts[-1].get(key) for key in ("verdict", "method", "evidence")):
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
    if report.get("schema_version") != 1:
        raise ValueError("Unsupported repair receipt schema.")
    if report.get("conservative") is not True:
        raise ValueError("A release receipt must conservatively cover unresolved candidates.")
    if report["solution"].get("revision") != report["revision"]:
        raise ValueError("The repair certificate belongs to a different evidence revision.")
    checks = {}
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
    return {"verified": all(checks.values()), "checks": checks,
            "verification_level": "original-evidence-and-certificate" if fixture is not None else "bundle-consistency-and-certificate",
            "certificate_checks": certificate_checks, "cost": report["solution"]["cost"],
            "excluded": sorted(excluded), "kept": sorted(kept),
            "revision": report["revision"], "conservative": report["conservative"], "scope": report["scope"]}


class HoldoutService:
    def __init__(self, fixture_path: str | Path | None = None, runtime_dir: str | Path | None = None, url: str | None = None):
        self.lock = threading.RLock()
        self.fixture_path = fixture_path
        self.fixture = load_fixture(fixture_path)
        self.catalog_path = Path(fixture_path) if fixture_path else PROJECT_ROOT / "fixtures" / "demo" / "dataset.json"
        if self.catalog_path.is_dir():
            self.catalog_path = self.catalog_path / "dataset.json"
        self.catalog_sha256 = _sha(self.catalog_path.read_bytes())
        self.run_id = uuid4().hex[:12]
        self.runtime_dir = Path(runtime_dir) if runtime_dir else PROJECT_ROOT / ".runtime" / "sessions" / self.run_id
        self.store = GraphStore(self.runtime_dir / "engine", url=url)
        try:
            self.store.seed(self.fixture)
            self.engine = self.store.capabilities()
            if not self.engine["native_maxflow"]:
                raise RuntimeError("Native algo.maxFlow is unavailable. Run the documented native setup; no fallback solver is used for HOLDOUT releases.")
        except BaseException:
            self.store.close(delete_graph=True)
            raise
        self.input_fingerprints = _fingerprints(self.fixture)
        self._cache: dict[tuple[int, bool], dict] = {}
        self._attempts: dict[str, set[str]] = {}
        self.release: dict | None = None
        self.release_dir: Path | None = None
        self.agent = {"status": "idle", "mode": None, "steps": 0, "message": "Ready to investigate."}
        self._event("seed_release", f"Captured {len(self.fixture['records'])} records and {len(self.fixture['candidates'])} candidate conflicts in FalkorDB.")

    def _event(self, tool: str, summary: str, **extra: Any) -> None:
        self.store.add_event({"timestamp": datetime.now(timezone.utc).isoformat(), "tool": tool, "summary": summary, **extra})

    def _unchanged(self) -> None:
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

    def next_candidate(self, bounds: dict | None = None) -> dict | None:
        with self.lock:
            bounds = bounds or self.bounds()
            by_id = {r["id"]: r for r in self.store.records()}
            lower_cover = set(bounds["lower"].get("excluded", []))
            options = []
            for c in self.store.candidates():
                if c["status"] != "unresolved" or "auto" in self._attempts.get(c["id"], set()):
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
                     "agent": deepcopy(self.agent), "provider_available": self.provider_available}
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
                    "release": self.release, "scope": SCOPE}

    def candidate(self, candidate_id: str) -> dict:
        for candidate in self.store.candidates():
            if candidate["id"] == candidate_id:
                return candidate
        raise KeyError(f"Unknown candidate: {candidate_id}")

    def inspect(self, record_id: str) -> dict:
        with self.lock:
            self._unchanged()
            result = inspect_record(self.fixture, record_id)
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
            c = self.candidate(candidate_id)
            return {"candidate": c, "train": inspect_record(self.fixture, c["train_id"]),
                    "eval": inspect_record(self.fixture, c["eval_id"]),
                    "lineage": {"train": self.store.provenance(c["train_id"]), "eval": self.store.provenance(c["eval_id"])},
                    "assessment": c.get("evidence")}

    def investigate(self, candidate_id: str, method: str = "auto", expected_revision: int | None = None) -> dict:
        if method not in {"auto", "exact", "normalized_ast", "provenance"}:
            raise ValueError("Unsupported investigation method.")
        with self.lock:
            self._unchanged()
            if expected_revision is not None and self.store.revision() != expected_revision:
                raise RevisionConflict("Evidence changed; reload the current revision.")
            c = self.candidate(candidate_id)
            if c["status"] != "unresolved":
                return c
            attempts = []
            methods = ("exact", "normalized_ast", "provenance") if method == "auto" else (method,)
            for selected in methods:
                if selected == "provenance":
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
            self.store.set_candidate(candidate_id, result["verdict"], result, expected_revision=self.store.revision())
            self._cache.clear()
            self.release = None
            self.release_dir = None
            return self.candidate(candidate_id)

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
            report = {"schema_version": 1, "project": "HOLDOUT", "run_id": self.run_id,
                      "fixture_id": self.fixture["id"], "revision": revision,
                      "created_at": datetime.now(timezone.utc).isoformat(), "conservative": True,
                      "scope": SCOPE, "engine": self.engine, "input_records": records,
                      "input_catalog_sha256": _catalog_digest(self.fixture),
                      "input_fingerprints": self.input_fingerprints, "candidates": self.store.candidates(),
                      "solution": solution, "artifact_sha256": hashes}
            (output / "repair.json").write_text(_json(report))
            verification = verify_release(output, self.fixture)
            self.release_dir = output
            self.release = {**verification, "files": list(ARTIFACT_NAMES)}
            self._event("stage_release", f"Wrote and verified {len(kept)} retained records in train/eval manifests; exclusion cost {solution['cost']}.", result=self.release)
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
