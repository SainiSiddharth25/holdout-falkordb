"""Bounded model-protocol tests. Every provider response uses MockTransport."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
import threading
from types import SimpleNamespace

import httpx
import pytest

import holdout.agent as agent_module
from holdout.agent import AgentBudgetExceeded, Budget, live_model


class AgentService:
    """Test the real agent and MCP protocol against a controlled domain boundary."""

    def __init__(self):
        self.lock = threading.RLock()
        self.agent = {"status": "idle", "steps": 0}
        self.release = None
        self.actions = []

    @property
    def provider_available(self):
        return bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("HOLDOUT_MODEL"))

    def compact_status(self):
        self.actions.append(("status",))
        return {"revision": 1, "next_candidate": None, "release": deepcopy(self.release)}

    def inspect(self, record_id):
        if record_id != "train-1":
            raise KeyError("Unknown captured record")
        self.actions.append(("inspect", record_id))
        return {"id": record_id, "content": "def solve(x): return x + 1"}

    def stage_release(self, conservative, expected_revision):
        self.actions.append(("stage", conservative, expected_revision))
        self.release = {"verified": True, "revision": 1, "conservative": conservative}
        return deepcopy(self.release)


@pytest.fixture(autouse=True)
def offline_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "holdout-test-key-never-valid")
    monkeypatch.setenv("HOLDOUT_MODEL", "holdout-test-model")
    monkeypatch.setenv("HOLDOUT_API_BASE", "https://holdout-provider.invalid/v1")
    for setting in ("HOLDOUT_MAX_PROVIDER_CALLS", "HOLDOUT_MAX_TOOL_CALLS", "HOLDOUT_MAX_TOKENS", "HOLDOUT_MAX_SECONDS"):
        monkeypatch.delenv(setting, raising=False)

    async def forbid_real_network(*args, **kwargs):
        pytest.fail("A protocol test attempted a real provider request")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_real_network)


def tool_call(name, arguments, call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}


def response(calls=None, content=None, tokens=10, *, include_usage=True):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    payload = {"choices": [{"message": message}]}
    if include_usage:
        payload["usage"] = {"total_tokens": tokens}
    return httpx.Response(200, json=payload)


def leaves(error):
    if isinstance(error, BaseExceptionGroup):
        return [item for child in error.exceptions for item in leaves(child)]
    return [error]


def expect_budget_stop(service, transport, budget):
    # An exception raised inside the SDK TaskGroup may retain its group wrapper.
    with pytest.raises((AgentBudgetExceeded, BaseExceptionGroup)) as stopped:
        asyncio.run(live_model(service, budget=budget, transport=transport))
    errors = leaves(stopped.value)
    assert errors and all(isinstance(error, AgentBudgetExceeded) for error in errors)
    assert service.agent["status"] == "failed"


def test_mocked_model_uses_real_mcp_catalog_calls_and_verified_completion():
    service = AgentService()
    requests = []
    snapshots = []

    def handler(request):
        assert str(request.url) == "https://holdout-provider.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer holdout-test-key-never-valid"
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "holdout-test-model"
        schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in payload["tools"]}
        assert "stage_release" in schemas and schemas["inspect_record"]["properties"]["record_id"]["type"] == "string"
        assert 0 < payload["max_completion_tokens"] <= 1200
        if len(requests) == 1:
            return response([tool_call("release_status", {})])
        if len(requests) == 2:
            last = payload["messages"][-1]
            assert last["role"] == "tool" and last["tool_call_id"] == "call-1"
            assert json.loads(last["content"])["revision"] == 1
            return response([tool_call("stage_release", {"expected_revision": 1}, "call-2")])
        assert json.loads(payload["messages"][-1]["content"])["verified"] is True
        return response(content="The conservative release is staged and verified.")

    result = asyncio.run(live_model(service, Budget(provider_calls=4, tool_calls=3, tokens=200000, seconds=10), httpx.MockTransport(handler), lambda: snapshots.append(deepcopy(service.agent))))
    assert len(requests) == 3
    assert service.actions == [("status",), ("stage", True, 1)]
    assert result["release"]["verified"] is True
    assert result["usage"]["provider_calls"] == 3 and result["usage"]["tool_calls"] == 2
    assert result["usage"]["tokens"] == 30
    assert service.agent["status"] == "completed" and snapshots[-1]["status"] == "completed"


@pytest.mark.parametrize("bad_call", [
    tool_call("run_shell", {"command": "echo unauthorized"}),
    tool_call("inspect_record", "not-json"),
    tool_call("inspect_record", '["train-1"]'),
    tool_call("inspect_record", {"record_id": "missing"}),
])
def test_invalid_model_tools_become_error_receipts_without_domain_actions(bad_call):
    service = AgentService()
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return response([bad_call])
        result = json.loads(payload["messages"][-1]["content"])
        assert result["error"]
        return response(content="Unable to continue.")

    expect_budget_stop(service, httpx.MockTransport(handler), Budget(provider_calls=2, tool_calls=3, tokens=200000, seconds=10))
    assert len(requests) == 2 and service.actions == [] and service.release is None
    assert service.agent["usage"]["tool_calls"] == 1


def test_textual_success_cannot_create_a_release_and_provider_budget_stops_it():
    service = AgentService()
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 2:
            assert "No current verified release exists" in payload["messages"][-1]["content"]
        return response(content="All done; the release is verified.")

    expect_budget_stop(service, httpx.MockTransport(handler), Budget(provider_calls=2, tool_calls=5, tokens=200000, seconds=10))
    assert len(requests) == 2 and service.release is None and service.actions == []


def test_tool_budget_blocks_later_actions_in_the_same_provider_response():
    service = AgentService()
    transport = httpx.MockTransport(lambda request: response([
        tool_call("inspect_record", {"record_id": "train-1"}, "inspect"),
        tool_call("stage_release", {}, "stage"),
    ]))
    expect_budget_stop(service, transport, Budget(provider_calls=3, tool_calls=1, tokens=200000, seconds=10))
    assert service.actions == [("inspect", "train-1")]
    assert service.release is None and service.agent["usage"]["tool_calls"] == 1


@pytest.mark.parametrize("include_usage", [True, False])
def test_token_limit_stops_tools_before_overbudget_response_can_mutate_state(include_usage):
    service = AgentService()
    transport = httpx.MockTransport(lambda request: response([tool_call("stage_release", {})], content="x" * 200001, tokens=200001, include_usage=include_usage))
    expect_budget_stop(service, transport, Budget(provider_calls=3, tool_calls=3, tokens=200000, seconds=10))
    assert service.actions == [] and service.release is None
    assert service.agent["usage"]["provider_calls"] == 1
    assert service.agent["usage"]["tool_calls"] == 0
    assert service.agent["usage"]["tokens"] > 200000
    if not include_usage:
        assert "estimate" in service.agent["usage"]["token_accounting"]


def test_input_token_reservation_blocks_an_unaffordable_provider_request():
    service = AgentService()
    transport = httpx.MockTransport(lambda request: pytest.fail("Input exceeded budget before this request"))
    expect_budget_stop(service, transport, Budget(provider_calls=3, tool_calls=3, tokens=10, seconds=10))
    assert service.actions == [] and service.release is None
    assert service.agent["usage"]["provider_calls"] == 0


def test_elapsed_budget_is_rechecked_after_provider_response(monkeypatch):
    service = AgentService()
    clock = {"now": 0.0}
    # Replace this module's clock binding, leaving asyncio's real clock intact.
    monkeypatch.setattr(agent_module, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def handler(request):
        clock["now"] = 6.0
        return response([tool_call("stage_release", {})])

    expect_budget_stop(service, httpx.MockTransport(handler), Budget(provider_calls=3, tool_calls=3, tokens=200000, seconds=5))
    assert service.actions == [] and service.release is None
    assert service.agent["usage"]["provider_calls"] == 1 and service.agent["usage"]["tool_calls"] == 0


def test_provider_error_stops_without_exposing_provider_body():
    service = AgentService()
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="sensitive-provider-response-contents"))
    with pytest.raises((RuntimeError, BaseExceptionGroup)) as stopped:
        asyncio.run(live_model(service, Budget(tokens=200000, seconds=10), transport))
    errors = leaves(stopped.value)
    assert errors and all("HTTP 401" in str(error) for error in errors)
    assert all("sensitive-provider-response-contents" not in str(error) for error in errors)
    assert service.agent["status"] == "failed" and service.actions == []


def test_missing_provider_configuration_never_sends_a_request(monkeypatch):
    service = AgentService()
    monkeypatch.delenv("OPENAI_API_KEY")
    transport = httpx.MockTransport(lambda request: pytest.fail("Unconfigured agent called provider"))
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        asyncio.run(live_model(service, transport=transport))
    assert service.agent["status"] == "idle"


@pytest.mark.parametrize("setting,value", [("HOLDOUT_MAX_PROVIDER_CALLS", "0"), ("HOLDOUT_MAX_TOOL_CALLS", "201"), ("HOLDOUT_MAX_TOKENS", "200001"), ("HOLDOUT_MAX_SECONDS", "1801")])
def test_environment_limits_reject_out_of_range_values(monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValueError, match=setting):
        Budget.from_env()
