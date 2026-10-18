"""Responses protocol checks using MockTransport and genuine local MCP sessions.

These tests never contact a model provider. Only the HOLDOUT domain boundary is
controlled; the investigator still discovers and invokes the real MCP catalog.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from holdout.agent import AgentBudgetExceeded, Budget, live_model
from test_agent import AgentService, leaves


@pytest.fixture(autouse=True)
def offline_responses_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "holdout-test-key-never-valid")
    monkeypatch.setenv("HOLDOUT_MODEL", "holdout-test-model")
    monkeypatch.setenv("HOLDOUT_API_BASE", "https://holdout-provider.invalid/v1")
    monkeypatch.setenv("HOLDOUT_API_MODE", "responses")
    for setting in (
        "HOLDOUT_MAX_OUTPUT_TOKENS", "HOLDOUT_REASONING_EFFORT",
        "HOLDOUT_MAX_PROVIDER_CALLS", "HOLDOUT_MAX_TOOL_CALLS",
        "HOLDOUT_MAX_TOKENS", "HOLDOUT_MAX_SECONDS",
    ):
        monkeypatch.delenv(setting, raising=False)

    async def forbid_real_network(*args, **kwargs):
        pytest.fail("A Responses protocol test attempted a real provider request")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_real_network)


def function_call(name, arguments, call_id="call-1"):
    return {
        "id": "fc_" + call_id,
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def message(text, phase="final_answer", item_id="msg_result"):
    return {
        "id": item_id,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "phase": phase,
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def response(output, *, status="completed", tokens=10, **extra):
    return httpx.Response(200, json={
        "id": "resp_test", "object": "response", "status": status,
        "output": output, "usage": {"total_tokens": tokens}, **extra,
    })


def run(service, handler, *, budget=None, snapshot=None):
    return asyncio.run(live_model(
        service,
        budget or Budget(provider_calls=5, tool_calls=5, tokens=200000, seconds=10),
        httpx.MockTransport(handler),
        snapshot,
    ))


def budget_stop(service, handler, budget):
    with pytest.raises((AgentBudgetExceeded, BaseExceptionGroup)) as stopped:
        run(service, handler, budget=budget)
    errors = leaves(stopped.value)
    assert errors and all(isinstance(error, AgentBudgetExceeded) for error in errors)
    assert service.agent["status"] == "failed"


def test_responses_preserves_reasoning_phases_calls_and_real_mcp_receipts():
    service = AgentService()
    requests, snapshots = [], []
    reasoning = {
        "id": "rs_first", "type": "reasoning", "summary": [],
        "encrypted_content": "opaque-encrypted-reasoning-test-value",
    }
    commentary = message("I will inspect the release status.", "commentary", "msg_first")
    status_call = function_call("release_status", {}, "status")
    stage_call = function_call("stage_release", {"expected_revision": 1}, "stage")

    def handler(request):
        assert str(request.url) == "https://holdout-provider.invalid/v1/responses"
        assert request.headers["authorization"] == "Bearer holdout-test-key-never-valid"
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "holdout-test-model"
        assert payload["store"] is False
        assert payload["max_output_tokens"] == 4096
        assert "max_completion_tokens" not in payload and "messages" not in payload
        assert "previous_response_id" not in payload
        assert "reasoning" not in payload
        assert isinstance(payload["instructions"], str) and payload["instructions"]
        schemas = {tool["name"]: tool for tool in payload["tools"]}
        assert all(tool["type"] == "function" and tool["strict"] is False and "function" not in tool for tool in schemas.values())
        assert schemas["inspect_record"]["parameters"]["properties"]["record_id"]["type"] == "string"
        assert "expected_revision" not in schemas["stage_release"]["parameters"].get("required", [])
        if len(requests) == 1:
            return response([reasoning, commentary, status_call])
        assert reasoning in payload["input"]
        assert commentary in payload["input"]
        assert status_call in payload["input"]
        receipts = {item["call_id"]: item for item in payload["input"] if item.get("type") == "function_call_output"}
        assert json.loads(receipts["status"]["output"])["revision"] == 1
        if len(requests) == 2:
            assert payload["input"][-1] == receipts["status"]
            return response([stage_call])
        assert stage_call in payload["input"]
        assert json.loads(receipts["stage"]["output"])["verified"] is True
        assert payload["input"][-1] == receipts["stage"]
        return response([message("The conservative release is staged and verified.")])

    result = run(service, handler, snapshot=lambda: snapshots.append(deepcopy(service.agent)))
    assert len(requests) == 3
    assert service.actions == [("status",), ("stage", True, 1)]
    assert result["release"]["verified"] is True
    assert result["usage"] == {"provider_calls": 3, "tool_calls": 2, "tokens": 30, "token_accounting": "provider-reported"}
    assert snapshots and all(item["mode"] == "llm" for item in snapshots)
    assert snapshots[-1]["status"] == "completed" and service.agent["status"] == "completed"


def test_responses_honors_output_limit_and_reasoning_effort(monkeypatch):
    service = AgentService()
    monkeypatch.setenv("HOLDOUT_MAX_OUTPUT_TOKENS", "2048")
    monkeypatch.setenv("HOLDOUT_REASONING_EFFORT", "low")
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["max_output_tokens"] == 2048
        assert payload["reasoning"] == {"effort": "low"}
        if len(requests) == 1:
            return response([function_call("stage_release", {})])
        return response([message("Verified release staged.")])

    assert run(service, handler)["release"]["verified"] is True
    assert len(requests) == 2


@pytest.mark.parametrize("status", ["incomplete", "failed"])
def test_responses_rejects_noncompleted_response_before_any_tools(status):
    service = AgentService()
    details = {"incomplete_details": {"reason": "max_output_tokens"}} if status == "incomplete" else {"error": {"message": "private provider diagnostic"}}
    with pytest.raises((RuntimeError, BaseExceptionGroup)) as stopped:
        run(service, lambda request: response([function_call("stage_release", {})], status=status, **details))
    errors = leaves(stopped.value)
    assert errors and all(isinstance(error, RuntimeError) for error in errors)
    assert all("private provider diagnostic" not in str(error) for error in errors)
    assert service.agent["status"] == "failed"
    assert service.actions == [] and service.release is None
    assert service.agent["usage"]["provider_calls"] == 1 and service.agent["usage"]["tool_calls"] == 0


def test_responses_validates_entire_output_batch_before_first_action():
    service = AgentService()
    invalid = function_call("stage_release", {}, "broken")
    del invalid["call_id"]
    with pytest.raises((RuntimeError, BaseExceptionGroup)) as stopped:
        run(service, lambda request: response([function_call("inspect_record", {"record_id": "train-1"}), invalid]))
    assert all("malformed function call" in str(error) for error in leaves(stopped.value))
    assert service.actions == [] and service.release is None
    assert service.agent["usage"]["tool_calls"] == 0


def test_responses_rejects_malformed_json_shape_before_tools():
    service = AgentService()
    with pytest.raises((RuntimeError, BaseExceptionGroup)) as stopped:
        run(service, lambda request: httpx.Response(200, json=[{"status": "completed"}]))
    assert all("malformed result" in str(error) for error in leaves(stopped.value))
    assert service.actions == [] and service.agent["status"] == "failed"


def test_responses_invalid_mode_and_output_config_fail_before_requests(monkeypatch):
    for setting, value in (
        ("HOLDOUT_API_MODE", "unknown_api"),
        ("HOLDOUT_MAX_OUTPUT_TOKENS", "0"),
        ("HOLDOUT_MAX_OUTPUT_TOKENS", "16001"),
        ("HOLDOUT_MAX_OUTPUT_TOKENS", "not-an-integer"),
    ):
        service = AgentService()
        with monkeypatch.context() as settings:
            settings.setenv(setting, value)
            with pytest.raises(ValueError, match=setting):
                run(service, lambda request: pytest.fail("Invalid configuration reached a provider request"))
        assert service.actions == [] and service.agent["status"] == "idle"


def test_responses_tool_budget_stops_later_action_in_same_output():
    service = AgentService()
    budget_stop(service, lambda request: response([
        function_call("inspect_record", {"record_id": "train-1"}, "inspect"),
        function_call("stage_release", {}, "stage"),
    ]), Budget(provider_calls=3, tool_calls=1, tokens=200000, seconds=10))
    assert service.actions == [("inspect", "train-1")] and service.release is None
    assert service.agent["usage"]["tool_calls"] == 1


def test_responses_input_token_reservation_prevents_unaffordable_request():
    service = AgentService()
    budget_stop(service, lambda request: pytest.fail("Unreserved request was sent"),
                Budget(provider_calls=3, tool_calls=3, tokens=10, seconds=10))
    assert service.actions == [] and service.release is None
    assert service.agent["usage"]["provider_calls"] == 0


def test_responses_token_overrun_stops_tool_actions():
    service = AgentService()
    budget_stop(service, lambda request: response([function_call("stage_release", {})], tokens=200001),
                Budget(provider_calls=3, tool_calls=3, tokens=200000, seconds=10))
    assert service.actions == [] and service.release is None
    assert service.agent["usage"]["tokens"] == 200001
    assert service.agent["usage"]["provider_calls"] == 1 and service.agent["usage"]["tool_calls"] == 0


def test_responses_textual_success_cannot_replace_verified_stage():
    service = AgentService()
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 2:
            assert "No current verified release exists" in payload["input"][-1]["content"]
        return response([message("All done; the release is verified.")])

    budget_stop(service, handler, Budget(provider_calls=2, tool_calls=3, tokens=200000, seconds=10))
    assert len(requests) == 2 and service.actions == [] and service.release is None


def test_responses_commentary_phase_cannot_finalize_a_verified_run():
    service = AgentService()
    service.release = {"verified": True, "revision": 1, "conservative": True}
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return response([message("I will now summarize the outcome.", "commentary")])
        assert "A current verified release exists" in payload["input"][-1]["content"]
        assert service.agent["status"] == "running"
        return response([message("The conservative release is verified; unresolved evidence remains scoped.")])

    result = run(service, handler)
    assert len(requests) == 2 and result["release"]["verified"] is True
    assert service.agent["status"] == "completed" and service.actions == []
