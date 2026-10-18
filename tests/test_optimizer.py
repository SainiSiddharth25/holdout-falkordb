from __future__ import annotations

from copy import deepcopy
from itertools import product
from pathlib import Path
import random
from uuid import uuid4

import pytest

from holdout.graph import GraphStore, RevisionConflict
from holdout.optimizer import CertificateError, NativeAlgorithmUnavailable, reference_solve, solve, validate_solution


def fixture_for(records, edges):
    return {"id": "test", "records": records, "candidates": [{"id": str(i), "train_id": t, "eval_id": e, "status": status} for i, (t, e, status) in enumerate(edges)]}


def example():
    records = [{"id": rid, "split": side, "cost": cost, "protected": False} for rid, side, cost in [("A", "train", 2), ("B", "train", 10), ("X", "eval", 10), ("Y", "eval", 1)]]
    return fixture_for(records, [("A", "X", "confirmed"), ("A", "Y", "confirmed"), ("B", "Y", "confirmed"), ("B", "X", "unresolved")])


def brute_force(records, candidates, conservative=False):
    active = [c for c in candidates if c["status"] == "confirmed" or (conservative and c["status"] == "unresolved")]
    choices = []
    for bits in product((0, 1), repeat=len(records)):
        selected = {r["id"] for r, chosen in zip(records, bits) if chosen}
        if any(r.get("protected") and r["id"] in selected for r in records):
            continue
        if any(c["train_id"] not in selected and c["eval_id"] not in selected for c in active):
            continue
        choices.append(sum(r["cost"] for r in records if r["id"] in selected))
    return min(choices) if choices else None


@pytest.fixture(scope="module")
def native_runtime(tmp_path_factory):
    path = tmp_path_factory.mktemp("holdout-native")
    manager = GraphStore(path, graph_name="holdout_test_manager_" + uuid4().hex)
    if not manager.capabilities()["native_maxflow"]:
        manager.close(delete_graph=True)
        pytest.skip("Native algo.maxFlow unavailable; run scripts/probe_engine.py --install-native-module.")
    yield path, manager
    manager.close(delete_graph=True)


@pytest.fixture
def native_store(native_runtime):
    path, _ = native_runtime
    store = GraphStore(path, graph_name="holdout_test_" + uuid4().hex)
    try:
        yield store
    finally:
        store.close(delete_graph=True)


def test_reference_matches_exhaustive_with_required_and_zero_cost_records():
    rng = random.Random(723)
    for _ in range(32):
        records = [{"id": f"{side}{i}", "split": side, "cost": rng.randrange(5), "protected": rng.random() < 0.2} for side in ["train", "eval"] for i in range(3)]
        edges = [(t["id"], e["id"], rng.choice(["confirmed", "unresolved", "rejected"])) for t in records[:3] for e in records[3:] if rng.random() < 0.5]
        fixture = fixture_for(records, edges)
        for conservative in [False, True]:
            expected = brute_force(records, fixture["candidates"], conservative)
            actual = reference_solve(records, fixture["candidates"], conservative)
            assert actual["cost"] == expected
            assert actual["feasible"] == (expected is not None)


def test_native_absence_is_not_a_reference_fallback():
    class Unavailable:
        def capabilities(self):
            return {"native_maxflow": False}
    with pytest.raises(NativeAlgorithmUnavailable):
        solve(Unavailable())


@pytest.mark.native
def test_native_example_and_decision_relevant_update(native_store):
    fixture = example()
    native_store.seed(fixture)
    lower, upper = solve(native_store), solve(native_store, conservative=True)
    assert lower["excluded"] == ["A", "Y"] and lower["cost"] == 3
    assert upper["cost"] == 11
    assert validate_solution(fixture["records"], fixture["candidates"], lower)["valid"]
    original_revision = native_store.revision()
    native_store.set_candidate("3", "rejected", {"reason": "shared scaffold"}, expected_revision=original_revision)
    assert solve(native_store, conservative=True)["cost"] == 3
    with pytest.raises(RevisionConflict):
        native_store.set_candidate("3", "confirmed", {}, expected_revision=original_revision)


@pytest.mark.native
def test_native_matches_exhaustive_and_reference(native_runtime):
    path, _ = native_runtime
    rng = random.Random(811)
    for _ in range(24):
        records = [{"id": f"{side}{i}", "split": side, "cost": rng.randrange(6), "protected": rng.random() < 0.2} for side in ["train", "eval"] for i in range(rng.randrange(1, 4))]
        ts, es = [r for r in records if r["split"] == "train"], [r for r in records if r["split"] == "eval"]
        edges = [(t["id"], e["id"], rng.choice(["confirmed", "unresolved", "rejected"])) for t in ts for e in es if rng.random() < 0.55]
        fixture = fixture_for(records, edges)
        store = GraphStore(path, graph_name="holdout_random_" + uuid4().hex)
        try:
            store.seed(fixture)
            for conservative in [False, True]:
                actual = solve(store, conservative)
                reference = reference_solve(records, fixture["candidates"], conservative)
                expected = brute_force(records, fixture["candidates"], conservative)
                assert actual["cost"] == reference["cost"] == expected
                assert actual["feasible"] == (expected is not None)
                assert validate_solution(records, fixture["candidates"], actual, conservative)["valid"]
        finally:
            store.close(delete_graph=True)


@pytest.mark.native
def test_certificate_rejects_forged_flow_capacities_and_exclusions(native_store):
    fixture = example()
    native_store.seed(fixture)
    result = solve(native_store)
    for mutation in ["capacity", "flow", "exclusion", "missing_edge"]:
        forged = deepcopy(result)
        if mutation == "capacity":
            forged["certificate"]["flow_edges"][0]["capacity"] += 1
        elif mutation == "flow":
            forged["certificate"]["flow_edges"][0]["flow"] += 1
        elif mutation == "exclusion":
            forged["excluded"] = []
        else:
            forged["certificate"]["flow_edges"].pop()
        with pytest.raises(CertificateError):
            validate_solution(fixture["records"], fixture["candidates"], forged)


@pytest.mark.native
def test_preserves_sentinel_graph_and_removes_only_solver_temporaries(native_store):
    sentinel_name = "ordinary_customer_" + uuid4().hex
    sentinel = native_store.db.select_graph(sentinel_name)
    sentinel.query("CREATE (:Customer {value:'preserve'})")
    try:
        native_store.seed(example())
        before = set(native_store.db.execute_command("GRAPH.LIST"))
        solve(native_store)
        after = set(native_store.db.execute_command("GRAPH.LIST"))
        assert before == after
        assert sentinel.query("MATCH (n:Customer) RETURN n.value").result_set == [["preserve"]]
    finally:
        sentinel.delete()


@pytest.mark.native
def test_provenance_preserves_record_metadata_and_edge_evidence(native_store):
    fixture = example()
    fixture["records"][0].update(solution_symbols=["solve"], content_path="records/a.py")
    fixture["entities"] = [{"id": "A", "kind": "record"}, {"id": "origin", "title": "Patch"}, {"id": "scaffold", "title": "Common test"}]
    fixture["links"] = [{"source": "A", "target": "origin", "relation": "DERIVED_FROM", "evidence_path": "evidence/a.json"}, {"source": "origin", "target": "scaffold", "relation": "USES_SCAFFOLD"}]
    native_store.seed(fixture)
    result = native_store.provenance("A")
    assert native_store.records() == sorted(fixture["records"], key=lambda r: r["id"])
    assert next(n for n in result["nodes"] if n["id"] == "A")["solution_symbols"] == ["solve"]
    assert any(p["nodes"] == ["A", "origin", "scaffold"] for p in result["paths"])
    assert any(e.get("evidence_path") == "evidence/a.json" for e in result["links"])


@pytest.mark.native
def test_empty_release(native_store):
    native_store.seed({"records": [], "candidates": []})
    result = solve(native_store)
    assert result["feasible"] and result["cost"] == 0 and result["excluded"] == []
    assert validate_solution([], [], result)["valid"]
