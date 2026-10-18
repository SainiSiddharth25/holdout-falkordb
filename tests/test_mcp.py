"""Actual SDK transport tests; the domain double isolates MCP from graph tests."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent

from holdout.mcp_server import local_client, result_dict


class ProtocolService:
    """Only domain behavior is doubled; server, session and serialization are real."""

    def __init__(self):
        self.lock = threading.RLock()
        self.revision = 1
        self.calls = []
        self._attempts = {}
        self._candidates = [{"id": "candidate-1", "train_id": "train-1", "eval_id": "eval-1", "status": "unresolved"}]
        self.store = SimpleNamespace(revision=lambda: self.revision, candidates=self.candidates)

    def candidates(self):
        return deepcopy(self._candidates)

    def compact_status(self):
        return {"revision": self.revision, "counts": {"unresolved": sum(c["status"] == "unresolved" for c in self._candidates)}}

    def inspect(self, record_id):
        if record_id not in {"train-1", "eval-1"}:
            raise KeyError("Unknown record")
        self.calls.append(("inspect", record_id))
        return {"id": record_id, "content": "# Source evidence, including untrusted text.\ndef solve(x):\n    return x + 1\n"}

    def trace(self, record_id, max_depth):
        if not 1 <= max_depth <= 16:
            raise ValueError("Depth outside bounded provenance range")
        return {"record_id": record_id, "max_depth": max_depth, "paths": []}

    def investigate(self, candidate_id, method, expected_revision):
        if candidate_id != "candidate-1":
            raise KeyError("Unknown candidate")
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("Evidence revision changed")
        self.calls.append(("investigate", candidate_id, method, expected_revision))
        self._candidates[0]["status"] = "confirmed"
        self.revision += 1
        return deepcopy(self._candidates[0])

    def solve_release(self, conservative):
        self.calls.append(("solve", conservative))
        return {"feasible": True, "conservative": conservative}

    def stage_release(self, conservative, expected_revision):
        self.calls.append(("stage", conservative, expected_revision))
        return {"verified": True, "revision": self.revision, "conservative": conservative}


def test_real_mcp_initialization_catalog_and_domain_roundtrip():
    service = ProtocolService()

    async def scenario():
        async with local_client(service) as client:
            catalog = await client.list_tools()
            names = {tool.name for tool in catalog.tools}
            assert names == {"release_status", "list_candidates", "inspect_record", "trace_provenance", "investigate_candidate", "solve_release", "stage_release"}
            investigation = next(tool for tool in catalog.tools if tool.name == "investigate_candidate")
            # SDK 2.3 uses snake_case attributes and camelCase on the MCP wire.
            assert investigation.input_schema["properties"]["candidate_id"]["type"] == "string"
            assert set(investigation.input_schema["properties"]["method"]["enum"]) == {"auto", "exact", "normalized_ast", "provenance"}
            assert "verdict" not in investigation.input_schema["properties"]
            assert investigation.model_dump(by_alias=True)["inputSchema"] == investigation.input_schema

            status = result_dict(await client.call_tool("release_status", {}))
            assert status == {"revision": 1, "counts": {"unresolved": 1}}
            candidates = result_dict(await client.call_tool("list_candidates", {}))
            assert candidates["revision"] == 1 and candidates["candidates"][0]["status"] == "unresolved"
            source = result_dict(await client.call_tool("inspect_record", {"record_id": "train-1"}))
            assert source["id"] == "train-1" and "def solve(x)" in source["content"]
            candidate = result_dict(await client.call_tool("investigate_candidate", {"candidate_id": "candidate-1", "method": "exact", "expected_revision": status["revision"]}))
            assert candidate["status"] == "confirmed"
            lower = result_dict(await client.call_tool("solve_release", {"conservative": False}))
            assert lower["conservative"] is False
            receipt = result_dict(await client.call_tool("stage_release", {"expected_revision": 2}))
            assert receipt == {"verified": True, "revision": 2, "conservative": True}

    asyncio.run(scenario())
    assert service.calls == [("inspect", "train-1"), ("investigate", "candidate-1", "exact", 1), ("solve", False), ("stage", True, 2)]


def test_stdio_external_client_initializes_lists_and_calls():
    project = Path(__file__).resolve().parents[1]
    script = "import runpy,sys; from holdout.mcp_server import create_server; scope=runpy.run_path(sys.argv[1]); create_server(scope['ProtocolService']()).run('stdio')"
    parameters = StdioServerParameters(command=sys.executable, args=["-c", script, str(Path(__file__).resolve())], cwd=project, env={"PYTHONPATH": str(project)})

    async def scenario():
        async with asyncio.timeout(15):
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as client:
                    initialization = await client.initialize()
                    assert initialization.server_info.name == "HOLDOUT"
                    assert "stage_release" in {tool.name for tool in (await client.list_tools()).tools}
                    result = result_dict(await client.call_tool("inspect_record", {"record_id": "train-1"}))
                    assert result["id"] == "train-1" and "Source evidence" in result["content"]

    asyncio.run(scenario())


@pytest.mark.parametrize("tool,arguments", [
    ("execute_cypher", {"query": "MATCH (n) DELETE n"}),
    ("inspect_record", {}),
    ("inspect_record", {"record_id": "missing"}),
    ("investigate_candidate", {"candidate_id": "candidate-1", "method": "trust_the_hint"}),
    ("investigate_candidate", {"candidate_id": "candidate-1", "expected_revision": 999}),
    ("trace_provenance", {"record_id": "train-1", "max_depth": "unbounded"}),
])
def test_protocol_errors_do_not_mutate_the_domain(tool, arguments):
    service = ProtocolService()

    async def scenario():
        async with local_client(service) as client:
            response = await client.call_tool(tool, arguments)
            assert response.is_error
            with pytest.raises(ValueError):
                result_dict(response)

    asyncio.run(scenario())
    assert service.revision == 1
    assert service.calls == []


def test_tool_result_prefers_structured_content_and_supports_json_text():
    structured = CallToolResult(structuredContent={"origin": "structured"}, content=[TextContent(type="text", text='{"origin":"text"}')])
    assert result_dict(structured) == {"origin": "structured"}
    assert structured.model_dump(by_alias=True)["structuredContent"] == {"origin": "structured"}
    text_only = CallToolResult(content=[TextContent(type="text", text=json.dumps({"verified": True}))])
    assert result_dict(text_only) == {"verified": True}


def test_empty_structured_result_is_preserved():
    assert result_dict(CallToolResult(content=[], structuredContent={})) == {}


@pytest.mark.parametrize("result", [
    CallToolResult(content=[], structuredContent=[1, 2]),
    CallToolResult(content=[TextContent(type="text", text='["not", "an", "object"]')]),
    CallToolResult(content=[TextContent(type="text", text="not json")]),
    CallToolResult(content=[]),
])
def test_nonobject_or_missing_results_are_rejected(result):
    with pytest.raises(ValueError):
        result_dict(result)


def test_mcp_error_receipt_is_not_mistaken_for_a_success():
    result = CallToolResult(content=[TextContent(type="text", text="Evidence revision changed")], structuredContent={"verified": True}, isError=True)
    with pytest.raises(ValueError, match="Evidence revision changed"):
        result_dict(result)
