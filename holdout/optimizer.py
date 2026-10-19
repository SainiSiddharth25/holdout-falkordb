"""Exact two-pool release repair using FalkorDB's native algo.maxFlow.

The Python code compiles capacities, recovers the residual cut, and checks a
primal/dual certificate. It never runs a hidden replacement flow algorithm.
reference_solve is an explicit independent NetworkX baseline, not a fallback.
"""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
import math
import time
from uuid import uuid4

from .graph import GraphError, GraphStore, RevisionConflict


class NativeAlgorithmUnavailable(GraphError):
    pass


class CertificateError(GraphError):
    pass


def _inputs(records: list[dict], candidates: list[dict], conservative: bool) -> tuple[dict, list[dict], int]:
    by_id = {r["id"]: r for r in records}
    if len(by_id) != len(records):
        raise ValueError("Duplicate record IDs.")
    for r in records:
        if r.get("split") not in {"train", "eval"} or type(r.get("cost")) is not int or r["cost"] < 0:
            raise ValueError("Records require nonnegative integer costs and a train/eval split.")
    accepted = {"confirmed", "unresolved"} if conservative else {"confirmed"}
    conflicts = []
    for c in candidates:
        if c["status"] in accepted:
            if by_id.get(c["train_id"], {}).get("split") != "train" or by_id.get(c["eval_id"], {}).get("split") != "eval":
                raise ValueError("A conflict must connect one train record and one eval record.")
            conflicts.append({"id": c["id"], "train_id": c["train_id"], "eval_id": c["eval_id"]})
    sentinel = sum(r["cost"] for r in records) + 1
    if sentinel >= 2**52:
        raise ValueError("Total cost is too large for exact integer representation in the native flow engine.")
    return by_id, sorted(conflicts, key=lambda c: c["id"]), sentinel


def _infeasible(by_id: dict, conflicts: list[dict]) -> dict | None:
    pair = next((c for c in conflicts if by_id[c["train_id"]].get("protected") and by_id[c["eval_id"]].get("protected")), None)
    if pair:
        return {"feasible": False, "excluded": [], "kept": sorted(by_id), "cost": None, "conflicts": conflicts, "certificate": {"valid": True, "type": "protected-endpoint-obstruction", "obstruction": pair, "explanation": "Both endpoints of an active conflict are required; no allowed exclusion covers this edge."}}
    return None


def _integer(value, description: str) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value != round(value):
        raise CertificateError(f"Native result contained nonintegral or invalid {description}.")
    return int(value)


def _certify(records: dict, conflicts: list[dict], edges: list[dict], positive_ids: list, positive_flows: list, maximum, sentinel: int) -> tuple[list[str], int, dict]:
    maximum = _integer(maximum, "maximum flow")
    if len(positive_ids) != len(positive_flows) or len(set(positive_ids)) != len(positive_ids):
        raise CertificateError("Native edge IDs and flow values are inconsistent.")
    all_ids = {e["id"] for e in edges}
    if any(edge_id not in all_ids for edge_id in positive_ids):
        raise CertificateError("Native result referenced an unknown edge.")
    returned = {key: _integer(value, "edge flow") for key, value in zip(positive_ids, positive_flows)}
    residual: dict[str, list[str]] = defaultdict(list)
    balance: dict[str, int] = defaultdict(int)
    # Edges absent from algo.maxFlow's positive-flow output still matter to
    # the residual cut. In particular, disconnected/zero-flow branches remain.
    for e in edges:
        flow = returned.get(e["id"], 0)
        if not 0 <= flow <= e["capacity"]:
            raise CertificateError("Flow violates an original edge capacity.")
        balance[e["source"]] -= flow
        balance[e["target"]] += flow
        if e["capacity"] > flow:
            residual[e["source"]].append(e["target"])
        if flow > 0:
            residual[e["target"]].append(e["source"])
    if balance["__source"] != -maximum or balance["__sink"] != maximum:
        raise CertificateError("Terminal flow does not equal the reported maximum.")
    if any(amount for key, amount in balance.items() if key not in {"__source", "__sink"}):
        raise CertificateError("Flow conservation failed at a record node.")
    reachable = {"__source"}
    todo = deque(reachable)
    while todo:
        for target in residual[todo.popleft()]:
            if target not in reachable:
                reachable.add(target)
                todo.append(target)
    if "__sink" in reachable:
        raise CertificateError("An augmenting path remains; the returned flow is not maximum.")
    cut_edges = [e for e in edges if e["source"] in reachable and e["target"] not in reachable]
    cut_capacity = sum(e["capacity"] for e in cut_edges)
    if cut_capacity != maximum:
        raise CertificateError("Cut capacity and feasible flow do not match.")
    if maximum >= sentinel:
        raise CertificateError("The cut crosses a forbidden-capacity edge despite a finite repair being available.")
    excluded = sorted(rid for rid, r in records.items() if (r["split"] == "train" and "record:" + rid not in reachable) or (r["split"] == "eval" and "record:" + rid in reachable))
    selected = set(excluded)
    if any(records[rid].get("protected") for rid in excluded):
        raise CertificateError("The cut excludes a protected record.")
    if any(c["train_id"] not in selected and c["eval_id"] not in selected for c in conflicts):
        raise CertificateError("The cut leaves an active conflict uncovered.")
    cost = sum(records[rid]["cost"] for rid in excluded)
    if cost != cut_capacity:
        raise CertificateError("Selected exclusion cost does not match the cut certificate.")
    certificate = {
        "valid": True, "type": "integral-maxflow-mincut", "max_flow": maximum,
        "cut_capacity": cut_capacity, "exclusion_cost": cost, "sentinel_capacity": sentinel,
        "checks": ["integer capacities", "capacity bounds", "flow conservation", "no residual augmenting path", "flow equals cut", "all active conflicts covered", "protected records retained", "exclusion cost equals cut"],
        "source_side": sorted(reachable), "cut_edges": cut_edges,
        "flow_edges": [{**e, "flow": returned.get(e["id"], 0)} for e in edges],
    }
    return excluded, cost, certificate


def solve(store: GraphStore, conservative: bool = False) -> dict:
    """Solve authoritative evidence only; never pass a planner scenario to export."""
    revision = store.revision()
    return _solve_snapshot(store, store.records(), store.candidates(), conservative, revision)


def solve_counterfactual(store: GraphStore, candidate_id: str, outcome: str,
                         expected_revision: int) -> dict:
    """Read-only single-claim scenario. No authoritative verdict is changed.

    Rejection is evaluated against the conservative graph; confirmation against
    the confirmed-only graph. Every scratch graph is unique and deleted after
    use. All resulting certificates are labelled hypothetical.
    """
    if outcome not in {"confirmed", "rejected"}:
        raise ValueError("A counterfactual outcome must be confirmed or rejected.")
    if store.revision() != expected_revision:
        raise RevisionConflict("Evidence changed before the planner scenario.")
    records, candidates = store.records(), store.candidates()
    found = [c for c in candidates if c["id"] == candidate_id]
    if len(found) != 1 or found[0]["status"] != "unresolved":
        raise ValueError("A planner scenario requires one currently unresolved candidate.")
    candidates = [{**c, "status": outcome} if c["id"] == candidate_id else c for c in candidates]
    result = _solve_snapshot(store, records, candidates, outcome == "rejected", expected_revision)
    return {**result, "hypothetical": True,
            "scenario": {"candidate_id": candidate_id, "assumed_status": outcome},
            "warning": "Counterfactual only: not an evidence verdict or an exportable repair."}


def _solve_snapshot(store: GraphStore, records: list[dict], candidates: list[dict],
                    conservative: bool, revision: int) -> dict:
    started = time.perf_counter()
    capability = store.capabilities()
    if not capability["native_maxflow"]:
        raise NativeAlgorithmUnavailable("This FalkorDB instance does not support algo.maxFlow. Install the verified native module using scripts/probe_engine.py --install-native-module or configure a capable external server. reference_solve is a separate benchmark, never a fallback.")
    by_id, conflicts, sentinel = _inputs(records, candidates, conservative)
    engine = "falkordb-native-maxflow"
    common = {"engine": engine, "native_procedure": "algo.maxFlow", "module_version": capability["module_version_text"], "revision": revision, "conservative": conservative}
    obstruction = _infeasible(by_id, conflicts)
    if obstruction:
        if store.revision() != revision:
            raise RevisionConflict("The evidence changed during optimization.")
        return {**obstruction, **common}
    if not records:
        # No relationship type exists in an empty FalkorDB graph. The empty
        # problem has a direct zero-flow/zero-cut certificate, stated openly.
        excluded, cost, certificate = _certify(by_id, conflicts, [], [], [], 0, sentinel)
        certificate["algorithm_invoked"] = False
        certificate["case"] = "empty release"
        if store.revision() != revision:
            raise RevisionConflict("The evidence changed during optimization.")
        return {"feasible": True, "excluded": excluded, "kept": [], "cost": cost, "conflicts": conflicts, "certificate": certificate, **common}
    network = store.db.select_graph("holdout_flow_" + uuid4().hex)
    try:
        nodes = [{"key": "__source"}, {"key": "__sink"}] + [{"key": "record:" + rid} for rid in sorted(by_id)]
        network.query("UNWIND $nodes AS item CREATE (:Flow {key:item.key})", {"nodes": nodes})
        arcs = []
        for rid, r in by_id.items():
            cap = sentinel if r.get("protected") else r["cost"]
            source, target = (("__source", "record:" + rid) if r["split"] == "train" else ("record:" + rid, "__sink"))
            arcs.append({"source": source, "target": target, "capacity": cap})
        # Duplicate candidate claims represent one incompatibility constraint.
        for train_id, eval_id in sorted({(c["train_id"], c["eval_id"]) for c in conflicts}):
            arcs.append({"source": "record:" + train_id, "target": "record:" + eval_id, "capacity": sentinel})
        if arcs:
            network.query("UNWIND $arcs AS item MATCH (s:Flow {key:item.source}),(t:Flow {key:item.target}) CREATE (s)-[:CAPACITY {capacity:item.capacity}]->(t)", {"arcs": arcs})
        original = network.query("MATCH (s)-[e:CAPACITY]->(t) RETURN id(e),s.key,t.key,e.capacity").result_set
        original = [{"id": int(eid), "source": s, "target": t, "capacity": int(cap)} for eid, s, t, cap in original]
        uploaded = time.perf_counter()
        rows = network.query("MATCH (s:Flow {key:'__source'}),(t:Flow {key:'__sink'}) CALL algo.maxFlow({sourceNodes:[s],targetNodes:[t],relationshipTypes:['CAPACITY'],nodeLabels:['Flow'],capacityProperty:'capacity'}) YIELD edges,edgeFlows,maxFlow RETURN maxFlow,[e IN edges | id(e)],edgeFlows").result_set
        computed = time.perf_counter()
        if len(rows) != 1:
            raise CertificateError("Native max-flow returned no unique result.")
        maximum, positive_ids, positive_flows = rows[0]
        excluded, cost, certificate = _certify(by_id, conflicts, original, positive_ids or [], positive_flows or [], maximum, sentinel)
        certificate["input_sha256"] = hashlib.sha256(json.dumps({"records": records, "conflicts": conflicts, "revision": revision, "conservative": conservative}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if store.revision() != revision:
            raise RevisionConflict("The evidence changed during optimization; discard this cut and solve the new revision.")
        return {"feasible": True, "excluded": excluded, "kept": sorted(set(by_id) - set(excluded)), "cost": cost, "conflicts": conflicts, "certificate": certificate, "timing_ms": {"snapshot_and_compile": round((uploaded - started) * 1000, 3), "native_maxflow": round((computed - uploaded) * 1000, 3), "certificate": round((time.perf_counter() - computed) * 1000, 3)}, **common}
    finally:
        # Delete only this invocation's UUID graph, never a named user graph.
        network.delete()


def reference_solve(records: list[dict], candidates: list[dict], conservative: bool = False) -> dict:
    """Explicit independent NetworkX baseline, unsuitable as an engine fallback."""
    import networkx as nx

    by_id, conflicts, sentinel = _inputs(records, candidates, conservative)
    obstruction = _infeasible(by_id, conflicts)
    if obstruction:
        return {**obstruction, "engine": "networkx-reference"}
    network = nx.DiGraph()
    source, sink = ("terminal", "source"), ("terminal", "sink")
    network.add_nodes_from([source, sink])
    for rid, r in by_id.items():
        v = ("record", rid)
        a, b = (source, v) if r["split"] == "train" else (v, sink)
        network.add_edge(a, b, capacity=sentinel if r.get("protected") else r["cost"])
    for c in conflicts:
        network.add_edge(("record", c["train_id"]), ("record", c["eval_id"]), capacity=sentinel)
    value, (reachable, _) = nx.minimum_cut(network, source, sink)
    excluded = sorted(rid for rid, r in by_id.items() if (r["split"] == "train" and ("record", rid) not in reachable) or (r["split"] == "eval" and ("record", rid) in reachable))
    return {"feasible": True, "excluded": excluded, "kept": sorted(set(by_id) - set(excluded)), "cost": int(value), "conflicts": conflicts, "engine": "networkx-reference"}


def validate_solution(records: list[dict], candidates: list[dict], result: dict, conservative: bool = False) -> dict:
    """Independently recheck an exported primal/dual certificate without a DB.

    Network capacities are reconstructed from the provided record policy and
    conflicts. Trusting capacities supplied by the certificate would permit a
    forged cheaper optimization problem, so both structure and values match.
    """
    by_id, conflicts, sentinel = _inputs(records, candidates, conservative)
    obstruction = _infeasible(by_id, conflicts)
    if not result.get("feasible"):
        if not obstruction:
            raise CertificateError("No protected-endpoint obstruction justifies infeasibility.")
        reported = result.get("certificate", {}).get("obstruction", {})
        if reported not in conflicts or not by_id[reported["train_id"]].get("protected") or not by_id[reported["eval_id"]].get("protected"):
            raise CertificateError("The reported infeasibility obstruction is invalid.")
        return {"valid": True, "feasible": False, "checks": ["protected-endpoint obstruction"]}
    if obstruction:
        raise CertificateError("A release with a protected-endpoint conflict cannot be feasible.")
    cert = result.get("certificate", {})
    edges = cert.get("flow_edges", [])
    expected: dict[tuple[str, str], int] = {}
    for rid, r in by_id.items():
        source, target = (("__source", "record:" + rid) if r["split"] == "train" else ("record:" + rid, "__sink"))
        expected[(source, target)] = sentinel if r.get("protected") else r["cost"]
    for c in conflicts:
        expected[("record:" + c["train_id"], "record:" + c["eval_id"])] = sentinel
    if len(edges) != len(expected) or len({e["id"] for e in edges}) != len(edges):
        raise CertificateError("Certificate network has missing, extra or duplicate edges.")
    supplied = {(e["source"], e["target"]): e["capacity"] for e in edges}
    if len(supplied) != len(edges) or supplied != expected:
        raise CertificateError("Certificate capacities do not match the declared release problem.")
    exclusions, cost, verified = _certify(by_id, conflicts, edges, [e["id"] for e in edges], [e["flow"] for e in edges], cert.get("max_flow"), sentinel)
    if exclusions != sorted(result.get("excluded", [])) or cost != result.get("cost"):
        raise CertificateError("Reported exclusions or cost do not match the certified cut.")
    if sorted(result.get("kept", [])) != sorted(set(by_id) - set(exclusions)):
        raise CertificateError("Reported kept records do not complement the exclusions.")
    if result.get("conflicts") != conflicts:
        raise CertificateError("Reported constraints differ from the requested evidence mode.")
    return {"valid": True, "feasible": True, "cost": cost, "checks": verified["checks"] + ["certificate network matches release inputs"]}
