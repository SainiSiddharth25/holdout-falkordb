"""Bounded decision-sensitivity planning, not evidence assessment.

Every scenario uses the real native solver through solve_counterfactual. A plan
may rank investigations; it cannot change evidence, costs, protected flags or a
release. Single-edge sensitivity is not expected value of information, and a
zero marginal effect never establishes irrelevance of jointly uncertain edges.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import time
from typing import Callable

from .graph import RevisionConflict
from .optimizer import solve_counterfactual, validate_solution

SCOPE = ("Cost optimality for this captured candidate graph, fixed separation policy and additive costs. "
         "Not proof of independent answers, candidate completeness, split balance or model quality.")
METHOD_EFFORT = {"record_identity": 1, "exact": 1, "normalized_ast": 2, "provenance": 3}
MAX_CANDIDATES = 128
MAX_REPORT_BYTES = 16 * 1024 * 1024


def decision_stop(bounds: dict) -> dict:
    """Only an equality of *finite* certified optima is a decision-stability proof."""
    lower, upper = bounds["lower"], bounds["upper"]
    if not lower["feasible"]:
        return {"stop": True, "code": "confirmed_infeasible", "cost_stable": False,
                "reason": "A confirmed conflict joins protected records. No feasible repair exists under this fixed policy."}
    if not upper["feasible"]:
        return {"stop": False, "code": "conservative_infeasible", "cost_stable": False,
                "reason": "An unresolved protected-endpoint conflict blocks conservative export. Evidence may resolve it; no export is currently allowed."}
    if lower["cost"] == upper["cost"]:
        return {"stop": True, "code": "cost_stable", "cost_stable": True,
                "reason": "Finite lower and upper costs agree. The current conservative repair is optimal for every resolution of the captured unresolved claims. Unknown claims stay unresolved."}
    return {"stop": False, "code": "gap_open", "cost_stable": False,
            "reason": "The cost gap is open. Single-edge sensitivities are a ranking heuristic, not proof that a zero-impact claim is irrelevant."}


def _cost(result: dict) -> int | None:
    return result["cost"] if result["feasible"] else None


def _brief(result: dict) -> dict:
    return {key: result.get(key) for key in ("feasible", "cost", "excluded", "revision")}


def build_plan(store, reviews: dict[str, dict], bounds: dict, *, run_id: str,
               catalog_digest: str, policy: str, max_candidates: int = 32,
               max_seconds: float = 30, clock: Callable[[], float] = time.monotonic) -> dict:
    if type(max_candidates) is not int or not 1 <= max_candidates <= MAX_CANDIDATES:
        raise ValueError(f"max_candidates must be an integer from 1 to {MAX_CANDIDATES}.")
    if not isinstance(max_seconds, (int, float)) or isinstance(max_seconds, bool) or not 0 < max_seconds <= 120:
        raise ValueError("Planner time budget must be between zero and 120 seconds.")
    started = clock()
    revision = store.revision()
    records, candidates = store.records(), store.candidates()
    if bounds["lower"]["revision"] != revision or bounds["upper"]["revision"] != revision:
        raise RevisionConflict("Bounds belong to a stale evidence revision.")
    # Validate the two proof objects against the actual snapshot before relying
    # on finite equality. No trust in model explanations or cached scalar costs.
    for name, conservative in (("lower", False), ("upper", True)):
        validate_solution(records, candidates, bounds[name], conservative)
    stop = decision_stop(bounds)
    lower_cost, upper_cost = _cost(bounds["lower"]), _cost(bounds["upper"])
    unresolved = [c for c in candidates if c["status"] == "unresolved"]
    by_id = {r["id"]: r for r in records}
    actionable, blocked = [], []
    for c in unresolved:
        pending = list(reviews[c["id"]]["pending_methods"])
        # Answer-policy identity is an observation, never a resolving method.
        verdict_methods = [m for m in pending if m != "record_identity"] if policy == "answer_leakage" else pending
        if verdict_methods:
            actionable.append((c, pending))
        else:
            blocked.append({"candidate_id": c["id"], "status": "unresolved",
                            "reason": "No untried applicable verdict-producing method in this captured snapshot. Obtain new source evidence to reconsider."})
    # Bounded scans preselect by the legacy endpoint priority, not hidden labels.
    # This is not a guarantee that the highest-impact edge was scanned.
    lower_cover = set(bounds["lower"].get("excluded", []))
    def preselect(item):
        c = item[0]; endpoints = [by_id[c["train_id"]], by_id[c["eval_id"]]]
        blocked_pair = all(r.get("protected") for r in endpoints)
        uncovered = not ({c["train_id"], c["eval_id"]} & lower_cover)
        finite = [r["cost"] for r in endpoints if not r.get("protected")]
        return (-int(blocked_pair), -int(uncovered), -min(finite, default=0), c["id"])
    actionable.sort(key=preselect)
    rows, proofs = [], {}
    scenario_solves, time_limited, report_limited = 0, False, False
    report_bytes = len(json.dumps({"records": records, "candidates": candidates, "bounds": bounds}, ensure_ascii=False).encode())
    if report_bytes > MAX_REPORT_BYTES:
        raise ValueError("Planner certificate inputs exceed the 16 MiB report budget. Use a smaller captured release for this stage.")
    if not stop["stop"]:
        for c, pending in actionable[:max_candidates]:
            if clock() - started >= max_seconds:
                time_limited = True
                break
            pair = {}
            # Check between calls: never interrupt a native solver mid-operation.
            for outcome in ("rejected", "confirmed"):
                if clock() - started >= max_seconds:
                    time_limited = True
                    break
                result = solve_counterfactual(store, c["id"], outcome, revision)
                scenario_solves += 1
                hypothetical = [{**x, "status": outcome} if x["id"] == c["id"] else x for x in candidates]
                validate_solution(records, hypothetical, result, outcome == "rejected")
                pair[outcome] = result
            if len(pair) != 2:
                break  # A one-sided evaluation is never presented as a full score.
            pair_bytes = len(json.dumps(pair, ensure_ascii=False).encode())
            if report_bytes + pair_bytes + 4096 > MAX_REPORT_BYTES:
                report_limited = True
                break
            report_bytes += pair_bytes + 4096  # Include bounded row-metadata allowance.
            reject, confirm = pair["rejected"], pair["confirmed"]
            saving = upper_cost - reject["cost"] if upper_cost is not None and reject["feasible"] else None
            uplift = confirm["cost"] - lower_cost if lower_cost is not None and confirm["feasible"] else None
            if saving is not None and saving < 0 or uplift is not None and uplift < 0:
                raise ValueError("Counterfactual bounds violated monotonicity.")
            effort = sum(METHOD_EFFORT[m] for m in pending)
            restores = not bounds["upper"]["feasible"] and reject["feasible"]
            obstruction = not confirm["feasible"]
            impact = max(saving or 0, uplift or 0)
            row = {"candidate_id": c["id"], "train_id": c["train_id"], "eval_id": c["eval_id"],
                   "method": "auto", "pending_methods": pending,
                   "rejection": {**_brief(reject), "saving": saving, "restores_feasibility": restores},
                   "confirmation": {**_brief(confirm), "uplift": uplift, "proves_infeasibility": obstruction},
                   "effort_units": effort, "score": round(impact / effort, 6),
                   "ranking_kind": "max(single-edge saving, uplift) / declared method-effort proxy",
                   "assumption": "Each branch assumes an outcome only for this claim. No probability or evidence verdict is predicted.",
                   "reason": ("Protected-endpoint obstruction requires attention." if restores or obstruction else
                              "No single-claim cost change; joint resolutions can still matter." if impact == 0 else
                              f"Rejection could save {saving} cost units; confirmation could raise the lower bound by {uplift}."),
                   "hypothetical": True}
            rows.append(row); proofs[c["id"]] = pair
    rows.sort(key=lambda r: (-int(r["rejection"]["restores_feasibility"] or r["confirmation"]["proves_infeasibility"]),
                            -Fraction(max(r["rejection"]["saving"] or 0, r["confirmation"]["uplift"] or 0), r["effort_units"]),
                            -(r["rejection"]["saving"] or 0), -(r["confirmation"]["uplift"] or 0), r["candidate_id"]))
    if not stop["stop"] and not actionable:
        stop = {"stop": True, "code": "evidence_exhausted", "cost_stable": False,
                "reason": "No untried verdict-producing methods remain. This is evidence exhaustion, not equality of the bounds or proof that missing evidence is harmless."}
    complete = len(rows) == len(actionable)
    if stop["stop"]:
        complete = len(actionable) == 0
    next_candidate = {"id": rows[0]["candidate_id"], "method": "auto", "priority_kind": "certified-counterfactual-heuristic"} if rows and not stop["stop"] else None
    binding = {"run_id": run_id, "revision": revision, "catalog_sha256": catalog_digest, "separation_policy": policy,
               "candidates_sha256": hashlib.sha256(json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    result = {"schema_version": 1, "binding": binding, "scope": SCOPE,
              "bounds": {"lower": _brief(bounds["lower"]), "upper": _brief(bounds["upper"]), "gap": bounds["gap"]},
              "stop": stop, "ranked": rows, "blocked": blocked, "next_candidate": next_candidate,
              "coverage": {"unresolved": len(unresolved), "actionable": len(actionable), "evaluated": len(rows),
                           "blocked": len(blocked), "not_evaluated": len(actionable) - len(rows),
                           "complete_actionable_scan": complete, "max_candidates": max_candidates,
                           "time_limited": time_limited, "report_limited": report_limited,
                           "note": "Ranking is only among evaluated claims. Partial scans and zero marginal scores are not stopping proofs."},
              "work": {"scenario_solves": scenario_solves, "baseline_certificates_checked": 2,
                       "seconds": round(clock() - started, 6), "time_budget_seconds": max_seconds,
                       "report_payload_budget_bytes": MAX_REPORT_BYTES,
                       "timing_scope": "Counterfactual analysis after baseline bounds are obtained; baseline solve time excluded.",
                       "budget_kind": "Checked between native operations; not a hard cancellation deadline."},
              "solver_backend": store.capabilities(),
              "effort_policy": {"method_units": METHOD_EFFORT, "kind": "uncalibrated planning proxy, not tokens, dollars or elapsed time"},
              "input_records": deepcopy(records), "input_candidates": deepcopy(candidates),
              "proofs": {"baseline": {k: bounds[k] for k in ("lower", "upper")}, "counterfactuals": proofs}}
    if store.revision() != revision:
        raise RevisionConflict("Evidence changed during planning; discard the plan.")
    result["plan_id"] = hashlib.sha256(json.dumps({"binding": binding, "ranked": rows, "stop": stop, "coverage": result["coverage"]}, sort_keys=True).encode()).hexdigest()[:24]
    return result


def public_plan(plan: dict, top: int = 8) -> dict:
    """Bounded model/UI projection. Full proofs are available in the local report."""
    result = deepcopy({k: v for k, v in plan.items() if k not in {"proofs", "ranked", "blocked", "input_records", "input_candidates"}})
    result["ranked"] = deepcopy(plan["ranked"][:top])
    result["blocked"] = deepcopy(plan["blocked"][:top])
    result["displayed"] = {"ranked": len(result["ranked"]), "blocked": len(result["blocked"]), "report_has_full_results": True}
    return result


def verify_plan(plan: dict, fixture: dict | None = None) -> dict:
    """Check original-input capacities, bounds and marginal-cost arithmetic.

    This verifies the mathematical scenarios, not the optimality of an inspection
    policy or authenticity of a self-contained report. Optional original evidence
    permits replay of the candidate assessments through the release verifier.
    """
    if plan.get("schema_version") != 1:
        raise ValueError("Unsupported planning report schema.")
    records, candidates = plan["input_records"], plan["input_candidates"]
    binding = plan["binding"]
    digest = hashlib.sha256(json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != binding["candidates_sha256"]:
        raise ValueError("Plan candidate snapshot hash does not match.")
    if fixture is not None:
        # Lazy import avoids a service/planning import cycle.
        from .service import _catalog_digest, _verify_evidence
        from .policies import separation_policy
        if _catalog_digest(fixture) != binding["catalog_sha256"] or {r["id"]: r for r in fixture["records"]} != {r["id"]: r for r in records} or separation_policy(fixture) != binding["separation_policy"]:
            raise ValueError("Plan differs from the independently retained input catalog.")
        _verify_evidence(fixture, candidates)
    baseline = plan["proofs"]["baseline"]
    for name, conservative in (("lower", False), ("upper", True)):
        if baseline[name].get("revision") != binding["revision"] or baseline[name].get("hypothetical"):
            raise ValueError("Baseline certificate is hypothetical or belongs to a stale revision.")
        validate_solution(records, candidates, baseline[name], conservative)
        if plan["bounds"][name] != _brief(baseline[name]):
            raise ValueError("Displayed bounds differ from their certificates.")
    lc, uc = _cost(baseline["lower"]), _cost(baseline["upper"])
    gap = uc - lc if lc is not None and uc is not None else None
    if plan["bounds"]["gap"] != gap:
        raise ValueError("Plan gap differs from the certified bounds.")
    stable = decision_stop(baseline)["cost_stable"]
    if plan["stop"].get("cost_stable") != stable or (plan["stop"].get("code") == "cost_stable") != stable:
        raise ValueError("A cost-stability claim is not supported by finite equal bounds.")
    if plan["stop"].get("code") == "cost_stable" and not plan["stop"].get("stop"):
        raise ValueError("Cost-stable stop flag is inconsistent.")
    rows = plan["ranked"]
    proof_map = plan["proofs"]["counterfactuals"]
    if len({r["candidate_id"] for r in rows}) != len(rows) or set(proof_map) != {r["candidate_id"] for r in rows}:
        raise ValueError("Plan rows and scenario certificates disagree.")
    by_id = {c["id"]: c for c in candidates}
    for row in rows:
        cid = row["candidate_id"]
        c = by_id[cid]
        if c["status"] != "unresolved" or row["train_id"] != c["train_id"] or row["eval_id"] != c["eval_id"] or row.get("hypothetical") is not True:
            raise ValueError("Scenario endpoints or unresolved-state binding differs.")
        for outcome in ("rejected", "confirmed"):
            result = proof_map[cid][outcome]
            if (result.get("hypothetical") is not True or result["scenario"] != {"candidate_id": cid, "assumed_status": outcome}
                    or result.get("revision") != binding["revision"]):
                raise ValueError("Scenario identity, status or revision is inconsistent.")
            inputs = [{**x, "status": outcome} if x["id"] == cid else x for x in candidates]
            validate_solution(records, inputs, result, outcome == "rejected")
            part = row["rejection" if outcome == "rejected" else "confirmation"]
            if any(part.get(k) != v for k, v in _brief(result).items()):
                raise ValueError("Displayed scenario differs from its certificate.")
        reject, confirm = proof_map[cid]["rejected"], proof_map[cid]["confirmed"]
        saving = uc - reject["cost"] if uc is not None and reject["feasible"] else None
        uplift = confirm["cost"] - lc if lc is not None and confirm["feasible"] else None
        if row["rejection"]["saving"] != saving or row["confirmation"]["uplift"] != uplift:
            raise ValueError("Reported sensitivity differs from the certified costs.")
        if (row["rejection"]["restores_feasibility"] != (not baseline["upper"]["feasible"] and reject["feasible"])
                or row["confirmation"]["proves_infeasibility"] != (not confirm["feasible"])):
            raise ValueError("Reported feasibility sensitivity differs from certificates.")
        effort = sum(METHOD_EFFORT[m] for m in row["pending_methods"])
        if effort < 1 or row["effort_units"] != effort or row["score"] != round(max(saving or 0, uplift or 0) / effort, 6):
            raise ValueError("Planning proxy or score is inconsistent.")
    ranked = sorted(rows, key=lambda r: (-int(r["rejection"]["restores_feasibility"] or r["confirmation"]["proves_infeasibility"]),
                                        -Fraction(max(r["rejection"]["saving"] or 0, r["confirmation"]["uplift"] or 0), r["effort_units"]),
                                        -(r["rejection"]["saving"] or 0), -(r["confirmation"]["uplift"] or 0), r["candidate_id"]))
    if rows != ranked or plan["coverage"]["evaluated"] != len(rows):
        raise ValueError("Ranking or evaluated-count mismatch.")
    if plan["next_candidate"] is not None and (plan["stop"]["stop"] or not rows or plan["next_candidate"]["id"] != rows[0]["candidate_id"]):
        raise ValueError("Recommended action disagrees with the current ranked plan.")
    return {"valid": True, "revision": binding["revision"], "cost_stable": stable,
            "scenario_pairs_checked": len(rows), "original_evidence_replayed": fixture is not None,
            "scope": "Certificate and sensitivity consistency. Not a digital signature or proof of best investigation strategy."}
