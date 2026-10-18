"""Native service, MCP execution, and exported-evidence integrity checks."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import httpx
import pytest

from holdout.agent import AgentBudgetExceeded, Budget, deterministic, live_model
from holdout.evidence import load_fixture
from holdout.graph import GraphStore, RevisionConflict
from holdout.mcp_server import local_client, result_dict
from holdout.optimizer import solve
from holdout.service import HoldoutService, verify_release


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "demo"


@pytest.fixture(scope="module")
def completed_service(tmp_path_factory):
    root = tmp_path_factory.mktemp("holdout-service")
    service = HoldoutService(runtime_dir=root / "runtime")
    try:
        initial = service.compact_status()
        assert initial["bounds"]["lower"]["cost"] == 0
        assert initial["bounds"]["upper"]["cost"] == 27
        receipt = asyncio.run(deterministic(service))
        assert receipt["verified"]
        yield service
    finally:
        service.close()


def release_copy(service, tmp_path):
    target = tmp_path / "release"
    shutil.copytree(service.release_dir, target)
    return target


def read_receipt(directory):
    return json.loads((directory / "repair.json").read_text())


def write_receipt(directory, report):
    (directory / "repair.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")


def contains_exception(error, kind):
    return isinstance(error, kind) or any(contains_exception(child, kind) for child in getattr(error, "exceptions", ()))


@pytest.mark.native
def test_actual_mcp_deterministic_release_preserves_unknowns_and_anchors(completed_service):
    service = completed_service
    state = service.compact_status()
    assert service.engine["native_maxflow"] is True
    assert state["counts"] == {"confirmed": 5, "rejected": 1, "unresolved": 2}
    assert state["bounds"]["lower"]["cost"] == 10
    assert state["bounds"]["upper"]["cost"] == 22
    assert state["next_candidate"] is None
    assert {candidate["id"] for candidate in service.store.candidates() if candidate["status"] == "unresolved"} == {"c406", "c408"}
    receipt = verify_release(service.release_dir, service.fixture)
    assert receipt["verified"] and receipt["conservative"]
    assert receipt["cost"] == 22
    assert set(receipt["excluded"]) == {"t101", "e202", "t103", "e206", "t105", "t107"}
    assert {"t106", "e203"} <= set(receipt["kept"])
    assert receipt["certificate_checks"]["valid"]
    assert any(event["tool"] == "stage_release" for event in service.store.events())


@pytest.mark.native
def test_mcp_catalog_has_no_verdict_query_or_output_path_control(completed_service):
    async def exercise():
        async with local_client(completed_service) as client:
            catalog = await client.list_tools()
            schemas = {tool.name: tool.input_schema for tool in catalog.tools}
            assert set(schemas) == {"release_status", "list_candidates", "inspect_record", "trace_provenance", "investigate_candidate", "solve_release", "stage_release"}
            assert not {"status", "verdict", "query", "code", "path"} & set(schemas["investigate_candidate"]["properties"])
            assert not {"path", "directory", "conservative"} & set(schemas["stage_release"]["properties"])
            assert "query" not in schemas["trace_provenance"]["properties"]
            inspected = result_dict(await client.call_tool("inspect_record", {"record_id": "t101"}))
            assert inspected["content_sha256"] and inspected["content"]
            bad = await client.call_tool("inspect_record", {"record_id": "../../etc/passwd"})
            with pytest.raises(ValueError):
                result_dict(bad)
    asyncio.run(exercise())


@pytest.mark.native
def test_stale_revision_and_nonconservative_stage_are_rejected(completed_service):
    service = completed_service
    revision = service.store.revision()
    with pytest.raises(RevisionConflict):
        service.stage_release(expected_revision=revision - 1)
    with pytest.raises(RevisionConflict):
        service.investigate("c406", expected_revision=revision - 1)
    with pytest.raises(ValueError):
        service.stage_release(conservative=False)
    assert service.store.revision() == revision
    assert service.release["verified"]


@pytest.mark.native
def test_altered_manifest_bytes_are_rejected(completed_service, tmp_path):
    directory = release_copy(completed_service, tmp_path)
    path = directory / "train.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        verify_release(directory, completed_service.fixture)


@pytest.mark.native
def test_changed_content_cannot_be_hidden_by_updating_manifest_hash(completed_service, tmp_path):
    directory = release_copy(completed_service, tmp_path)
    report = read_receipt(directory)
    path = directory / "train.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["content"] += "\n# altered after release\n"
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    path.write_bytes(payload)
    report["artifact_sha256"]["train.jsonl"] = hashlib.sha256(payload).hexdigest()
    write_receipt(directory, report)
    with pytest.raises(ValueError):
        verify_release(directory, completed_service.fixture)


@pytest.mark.native
def test_changed_manifest_policy_cannot_be_hidden_by_updating_hash(completed_service, tmp_path):
    directory = release_copy(completed_service, tmp_path)
    report = read_receipt(directory)
    path = directory / "train.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["cost"] += 100
    rows[0]["protected"] = not rows[0]["protected"]
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    path.write_bytes(payload)
    report["artifact_sha256"]["train.jsonl"] = hashlib.sha256(payload).hexdigest()
    write_receipt(directory, report)
    with pytest.raises(ValueError):
        verify_release(directory, completed_service.fixture)


@pytest.mark.native
def test_changed_source_capture_is_rejected(completed_service, tmp_path):
    copied = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, copied)
    fixture = load_fixture(copied)
    source = copied / "captures" / "capture-31.md"
    source.write_text(source.read_text() + "\nModified capture.\n")
    with pytest.raises(ValueError):
        verify_release(completed_service.release_dir, fixture)


@pytest.mark.native
def test_catalog_mutation_after_capture_blocks_inspection_and_staging(tmp_path):
    copied = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, copied)
    service = HoldoutService(fixture_path=copied, runtime_dir=tmp_path / "catalog-runtime")
    try:
        catalog_path = copied / "dataset.json"
        catalog = json.loads(catalog_path.read_text())
        catalog["records"][0]["cost"] += 1
        catalog_path.write_text(json.dumps(catalog))
        with pytest.raises(ValueError):
            service.inspect("t101")
        with pytest.raises(ValueError):
            service.stage_release()
        assert service.release is None
    finally:
        service.close()


@pytest.mark.native
def test_nonconservative_receipt_is_not_a_verified_release(completed_service, tmp_path):
    directory = release_copy(completed_service, tmp_path)
    report = read_receipt(directory)
    report["conservative"] = False
    write_receipt(directory, report)
    with pytest.raises(ValueError):
        verify_release(directory, completed_service.fixture)


@pytest.mark.native
def test_omitted_rejected_candidate_still_breaks_original_catalog_binding(completed_service, tmp_path):
    directory = release_copy(completed_service, tmp_path)
    report = read_receipt(directory)
    # This candidate contributes no capacity edge. The mathematical certificate
    # is unchanged, so only binding to the original evidence catalog catches it.
    report["candidates"] = [candidate for candidate in report["candidates"] if candidate["id"] != "c405"]
    write_receipt(directory, report)
    with pytest.raises(ValueError):
        verify_release(directory, completed_service.fixture)


@pytest.mark.native
def test_unresolved_cannot_be_relabeled_confirmed_without_support(completed_service, tmp_path):
    directory = release_copy(completed_service, tmp_path)
    report = read_receipt(directory)
    # Both statuses are active in the conservative network. The cut remains
    # mathematically correct, but the evidence does not justify this new claim.
    next(candidate for candidate in report["candidates"] if candidate["id"] == "c406")["status"] = "confirmed"
    write_receipt(directory, report)
    with pytest.raises(ValueError):
        verify_release(directory, completed_service.fixture)


@pytest.mark.native
def test_self_consistent_cut_for_forged_clearance_is_rejected(completed_service, tmp_path):
    directory = release_copy(completed_service, tmp_path)
    report = read_receipt(directory)
    candidate = next(item for item in report["candidates"] if item["id"] == "c406")
    candidate["status"] = "rejected"
    if isinstance(candidate.get("evidence"), dict):
        candidate["evidence"]["verdict"] = "rejected"
        for attempt in candidate["evidence"].get("attempts", []):
            attempt["verdict"] = "rejected"
    forged_fixture = deepcopy(completed_service.fixture)
    forged_fixture["candidates"] = report["candidates"]
    store = GraphStore(completed_service.store.runtime_dir)
    try:
        store.seed(forged_fixture)
        report["solution"] = solve(store, conservative=True)
        report["revision"] = report["solution"]["revision"]
    finally:
        store.close(delete_graph=True)
    kept = set(report["solution"]["kept"])
    for split in ("train", "eval"):
        rows = []
        for record in report["input_records"]:
            if record["id"] in kept and record["split"] == split:
                content = (Path(completed_service.fixture["_root"]) / record["content_path"]).read_text()
                rows.append({**record, "content": content})
        payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
        name = split + ".jsonl"
        (directory / name).write_bytes(payload)
        report["artifact_sha256"][name] = hashlib.sha256(payload).hexdigest()
    write_receipt(directory, report)
    assert report["solution"]["cost"] < completed_service.release["cost"]
    with pytest.raises(ValueError):
        verify_release(directory, completed_service.fixture)


@pytest.mark.native
def test_live_provider_token_overrun_stops_before_any_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    monkeypatch.setenv("HOLDOUT_MODEL", "local-test-model")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"usage": {"total_tokens": 200001}, "choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "stage_release", "arguments": "{}"}}]}}]})
    service = HoldoutService(runtime_dir=tmp_path / "token-budget")
    try:
        with pytest.raises(Exception) as error:
            asyncio.run(live_model(service, Budget(provider_calls=2, tool_calls=2, tokens=100000, seconds=30), transport=httpx.MockTransport(handler)))
        assert contains_exception(error.value, AgentBudgetExceeded)
        assert len(requests) == 1
        assert service.agent["usage"]["tool_calls"] == 0
        assert service.release is None
    finally:
        service.close()


@pytest.mark.native
def test_live_preflight_reservation_can_stop_before_provider_request(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    monkeypatch.setenv("HOLDOUT_MODEL", "local-test-model")
    requests = []
    def handler(request):
        requests.append(request)
        raise AssertionError("Input reservation should stop this request before sending it.")
    service = HoldoutService(runtime_dir=tmp_path / "preflight-budget")
    try:
        with pytest.raises(Exception) as error:
            asyncio.run(live_model(service, Budget(provider_calls=1, tool_calls=1, tokens=1, seconds=30), transport=httpx.MockTransport(handler)))
        assert contains_exception(error.value, AgentBudgetExceeded)
        assert not requests
        assert service.agent["usage"]["provider_calls"] == 0
        assert service.release is None
    finally:
        service.close()


@pytest.mark.native
def test_live_tool_batch_cannot_cross_action_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    monkeypatch.setenv("HOLDOUT_MODEL", "local-test-model")
    def handler(request):
        return httpx.Response(200, json={"usage": {"total_tokens": 100}, "choices": [{"message": {"role": "assistant", "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "release_status", "arguments": "{}"}},
            {"id": "call-2", "type": "function", "function": {"name": "stage_release", "arguments": "{}"}},
        ]}}]})
    service = HoldoutService(runtime_dir=tmp_path / "tool-budget")
    try:
        with pytest.raises(Exception) as error:
            asyncio.run(live_model(service, Budget(provider_calls=2, tool_calls=1, tokens=100000, seconds=30), transport=httpx.MockTransport(handler)))
        assert contains_exception(error.value, AgentBudgetExceeded)
        assert service.agent["usage"]["tool_calls"] == 1
        assert service.release is None
        assert service.store.revision() == 1
    finally:
        service.close()
