"""Deterministic baseline and bounded live-model investigator, both using MCP."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import time
from typing import Callable

import httpx

from .mcp_server import INSTRUCTIONS, local_client, result_dict
from .service import HoldoutService


@dataclass(frozen=True)
class Budget:
    provider_calls: int = 24
    tool_calls: int = 36
    tokens: int = 100000
    seconds: float = 180

    @classmethod
    def from_env(cls) -> "Budget":
        def read(name: str, default: int, maximum: int) -> int:
            value = int(os.environ.get(name, default))
            if value < 1 or value > maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}.")
            return value
        return cls(read("HOLDOUT_MAX_PROVIDER_CALLS", 24, 100), read("HOLDOUT_MAX_TOOL_CALLS", 36, 200),
                   read("HOLDOUT_MAX_TOKENS", 100000, 200000), read("HOLDOUT_MAX_SECONDS", 180, 1800))


class AgentBudgetExceeded(RuntimeError):
    pass


def _status(service: HoldoutService, **values) -> None:
    with service.lock:
        service.agent.update(values)


async def deterministic(service: HoldoutService, snapshot: Callable[[], None] | None = None) -> dict:
    """A reproducible fixed policy; it does not claim to be a live LLM."""
    _status(service, status="running", mode="deterministic", steps=0, message="Investigating through MCP with the deterministic policy.")
    try:
        async with local_client(service) as client:
            count = 0
            while True:
                status = result_dict(await client.call_tool("release_status", {}))
                next_candidate = status["next_candidate"]
                if next_candidate is None:
                    break
                result_dict(await client.call_tool("investigate_candidate", {"candidate_id": next_candidate["id"], "method": "auto", "expected_revision": status["revision"]}))
                count += 1
                _status(service, steps=count, message=f"Investigated {count} candidate hypotheses through MCP.")
                if snapshot:
                    snapshot()
            result_dict(await client.call_tool("solve_release", {"conservative": True}))
            receipt = result_dict(await client.call_tool("stage_release", {}))
            _status(service, status="completed", message="Conservative repair exported and verified; unresolved evidence remains explicitly scoped.")
            if snapshot:
                snapshot()
            return receipt
    except BaseException:
        _status(service, status="failed", message="Investigation stopped; inspect the backend error before proceeding.")
        raise


async def graph_guided(service: HoldoutService, snapshot: Callable[[], None] | None = None,
                       max_investigations: int | None = None) -> dict:
    """Deterministic decision-directed policy through MCP; no model/API key.

    The legacy deterministic runner remains an exhaustive endpoint baseline.
    This runner uses only current plans and real evidence actions. A closed gap
    saves inspection work; the conservative certificate still covers unknowns.
    """
    limit = int(os.environ.get("HOLDOUT_GUIDED_MAX_INVESTIGATIONS", "24")) if max_investigations is None else max_investigations
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Guided investigation limit must be from 1 to 200.")
    _status(service, status="running", mode="graph_guided", steps=0, message="Planning investigations through native FalkorDB counterfactuals and MCP.")
    count = 0
    stop_code = "investigation_budget"
    try:
        async with local_client(service) as client:
            for _ in range(limit):
                status = result_dict(await client.call_tool("release_status", {}))
                plan = result_dict(await client.call_tool("plan_investigation", {"expected_revision": status["revision"]}))
                if snapshot:
                    snapshot()
                if plan["stop"]["stop"]:
                    stop_code = plan["stop"]["code"]
                    break
                choice = plan["next_candidate"]
                if choice is None:
                    stop_code = "planner_scan_budget"
                    break
                result_dict(await client.call_tool("investigate_candidate", {
                    "candidate_id": choice["id"], "method": "auto", "expected_revision": plan["binding"]["revision"]}))
                count += 1
                _status(service, steps=count, message=f"Graph-guided investigator completed {count} evidence batches.")
                if snapshot:
                    snapshot()
            # Inspect final state even after the last allowed action. This is
            # needed to label a just-closed gap correctly, not as exhaustion.
            status = result_dict(await client.call_tool("release_status", {}))
            if status["bounds"]["upper"]["feasible"] and status["gap"] == 0:
                stop_code = "cost_stable"
            if stop_code == "cost_stable":
                result_dict(await client.call_tool("plan_investigation", {"expected_revision": status["revision"]}))
            result_dict(await client.call_tool("solve_release", {"conservative": True}))
            receipt = result_dict(await client.call_tool("stage_release", {"expected_revision": status["revision"]}))
            message = ("Finite repair-cost bounds agree; further captured-claim resolution cannot lower this conservative optimum. Unknowns remain explicit. "
                       if stop_code == "cost_stable" else
                       "Investigation stopped at " + stop_code + "; this is not proof that all claims are resolved. ")
            _status(service, status="completed", investigation_stop=stop_code, steps=count,
                    message=message + "Conservative release staged and independently verified.")
            if snapshot:
                snapshot()
            return receipt
    except BaseException:
        _status(service, status="failed", investigation_stop=stop_code,
                message="Graph-guided investigation stopped before a verified release; inspect current evidence and error.")
        raise


async def coordinated(service: HoldoutService, snapshot: Callable[[], None] | None = None) -> dict:
    """Two deterministic MCP specialists share an exclusive FalkorDB task queue.

    Not two LLMs: deliberately testable coordination baseline. A code worker
    checks solution comparisons and hands unresolved work to provenance; a
    provenance worker checks graph lineage. Neither can transfer a verdict.
    """
    _status(service,status='running',mode='coordinated',steps=0,
            message='Two specialized MCP investigators coordinating through FalkorDB tasks.')
    total = 0
    async def specialist(role: str) -> int:
        completed = 0
        owner='sentinel-' + role
        async with local_client(service) as client:
            while True:
                claimed=result_dict(await client.call_tool('claim_investigation',
                            {'role':role,'owner':owner,'lease_seconds':120})).get('claim')
                if not claimed:
                    break
                cid=claimed['candidate_id']
                token=claimed['lease_token']
                # Use public evidence-method applicability to avoid unsupported
                # comparisons and not repeat already exhausted methods.
                current = service.candidate(cid)
                choices = service.review_candidate(current)['pending_methods']
                allowed = {'record_identity','exact','normalized_ast'} if role=='code' else {'provenance'}
                for method in choices:
                    if method not in allowed:
                        continue
                    result_dict(await client.call_tool('investigate_candidate',
                               {'candidate_id':cid,'method':method,'lease_token':token}))
                    if service.candidate(cid)['status']!='unresolved':
                        break
                next_choice=service._task_decisions()[cid]
                handoff = next_choice['role'] if not next_choice['done'] else None
                result_dict(await client.call_tool('finish_investigation',
                            {'candidate_id':cid,'lease_token':token,'handoff_role':handoff}))
                completed+=1
                _status(service,steps=service.agent.get('steps',0)+1,
                        message=f'{role} specialist finished or handed off {cid}.')
                if snapshot:
                    snapshot()
        return completed
    try:
        # Round based: if a code worker hands off while provenance is exiting,
        # a later round will still pick it up. One claimed task per candidate.
        for _ in range(len(service.store.candidates())*2+2):
            done = await asyncio.gather(specialist('code'), specialist('provenance'))
            total += sum(done)
            if not any(done):
                break
        outstanding=[t for t in service.coordination_status()['tasks'] if t['state']!='done']
        if outstanding:
            raise RuntimeError('Coordinated investigators left unfinished graph tasks; no release staged.')
        async with local_client(service) as client:
            result_dict(await client.call_tool('solve_release', {'conservative':True}))
            receipt=result_dict(await client.call_tool('stage_release', {}))
        _status(service,status='completed',steps=total,mode='coordinated',
                message='Two MCP specialists completed graph-backed claims and handoffs; conservative release verified.')
        return receipt
    except BaseException:
        _status(service,status='failed',mode='coordinated',
                message='Coordinated run stopped; evidence survives and expired claims are fenced.')
        raise

def _bounded_payload(value: dict, limit: int = 16000) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= limit:
        return text
    # Preserve a clearly marked evidence excerpt, rather than silently dropping
    # bytes and presenting malformed JSON as a complete result.
    return json.dumps({"truncated": True, "excerpt": text[:limit], "note": "Result exceeded the agent context budget; inspect a narrower record or candidate."})


def _provider_settings() -> tuple[str, int]:
    api_mode = os.environ.get("HOLDOUT_API_MODE", "chat_completions")
    if api_mode not in {"chat_completions", "responses"}:
        raise ValueError("HOLDOUT_API_MODE must be chat_completions or responses.")
    try:
        output_limit = int(os.environ.get("HOLDOUT_MAX_OUTPUT_TOKENS", 4096 if api_mode == "responses" else 1200))
    except ValueError:
        raise ValueError("HOLDOUT_MAX_OUTPUT_TOKENS must be an integer between 1 and 16000.") from None
    if not 1 <= output_limit <= 16000:
        raise ValueError("HOLDOUT_MAX_OUTPUT_TOKENS must be between 1 and 16000.")
    return api_mode, output_limit


def _responses_output(payload: dict) -> tuple[list[dict], list[dict], str]:
    """Validate a complete response before allowing any of its tool actions."""
    if payload.get("status") == "incomplete":
        details = payload.get("incomplete_details")
        if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
            raise RuntimeError("Model response reached max_output_tokens; no tool actions were taken. Increase HOLDOUT_MAX_OUTPUT_TOKENS within the run budget.")
        raise RuntimeError("Model endpoint returned an incomplete Responses result; no tool actions were taken.")
    if payload.get("status") != "completed" or payload.get("error") is not None or payload.get("incomplete_details") is not None:
        raise RuntimeError("Model endpoint did not return a successful completed Responses result; no tool actions were taken.")
    output = payload.get("output")
    if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
        raise RuntimeError("Model endpoint returned malformed Responses output; no tool actions were taken.")
    calls = []
    call_ids = set()
    final_text = []
    for item in output:
        if item.get("type") in ("message", "function_call") and item.get("status") not in (None, "completed"):
            raise RuntimeError("Model endpoint returned unfinished Responses output; no tool actions were taken.")
        if item.get("type") == "function_call":
            call_id, name, arguments = item.get("call_id"), item.get("name"), item.get("arguments")
            if not isinstance(call_id, str) or not call_id or call_id in call_ids or not isinstance(name, str) or not name or not isinstance(arguments, str):
                raise RuntimeError("Model endpoint returned a malformed function call; no tool actions were taken.")
            call_ids.add(call_id)
            calls.append({"id": call_id, "name": name, "arguments": arguments})
        elif item.get("type") == "message":
            content = item.get("content")
            if not isinstance(content, list) or any(not isinstance(part, dict) for part in content):
                raise RuntimeError("Model endpoint returned malformed message content; no tool actions were taken.")
            if any(part.get("type") == "refusal" for part in content):
                raise RuntimeError("Model endpoint declined to continue the investigation; no tool actions were taken.")
            for part in content:
                if part.get("type") == "output_text":
                    if not isinstance(part.get("text"), str):
                        raise RuntimeError("Model endpoint returned malformed message text; no tool actions were taken.")
                    if item.get("phase") in (None, "final_answer"):
                        final_text.append(part["text"])
    # Keep every original output item, including reasoning/encrypted content and
    # message phase, for the next stateless request. Only function_call items
    # become actions, and those still pass through the actual MCP boundary.
    return output, calls, "\n".join(final_text)


async def live_model(service: HoldoutService, budget: Budget | None = None,
                     transport: httpx.AsyncBaseTransport | None = None,
                     snapshot: Callable[[], None] | None = None) -> dict:
    """Use a configured Chat Completions or Responses endpoint for MCP tools.

    Credentials and endpoint come only from the operator's environment. The
    model cannot supply paths, database queries, URLs, evidence verdicts or code
    execution. Provider usage is measured when supplied; otherwise conservatively
    estimated from complete request/response byte counts plus framing allowance.
    Elapsed time is a stopping budget; an in-flight native operation may finish.
    """
    if not service.provider_available:
        raise ValueError("Set OPENAI_API_KEY and HOLDOUT_MODEL on the backend to enable the live investigator.")
    api_mode, output_limit = _provider_settings()
    budget = budget or Budget.from_env()
    route = "/responses" if api_mode == "responses" else "/chat/completions"
    endpoint = os.environ.get("HOLDOUT_API_BASE", "https://api.openai.com/v1").rstrip("/") + route
    model = os.environ["HOLDOUT_MODEL"]
    reasoning_effort = os.environ.get("HOLDOUT_REASONING_EFFORT")
    started = time.monotonic()
    usage = {"provider_calls": 0, "tool_calls": 0, "tokens": 0, "compactions": 0, "token_accounting": "provider-reported"}
    _status(service, status="running", mode="llm", api_mode=api_mode, steps=0, message=f"Live investigator: {model}.")
    try:
        async with local_client(service) as client:
            catalog = await client.list_tools()
            functions = [{"name": tool.name, "description": tool.description or "", "parameters": tool.input_schema} for tool in catalog.tools]
            if api_mode == "responses":
                # Preserve MCP's optional/default arguments instead of allowing
                # the provider to normalize them into a strict required schema.
                schemas = [{"type": "function", **function, "strict": False} for function in functions]
            else:
                schemas = [{"type": "function", "function": function} for function in functions]
            allowed = {function["name"] for function in functions}
            instructions = INSTRUCTIONS + "\nUse tools to investigate, solve, and stage a conservative verified release. You may stop investigating when remaining unknowns are explicitly covered. Do not repeatedly call an already exhausted method. Inspect status first, call recall_memory to review valid past findings, then call plan_investigation. For unfamiliar claims, suggest_procedures may propose methods but never verdicts. Prefer its ranked candidates and use auto for a bounded applicable evidence sequence. Replan after real verdict updates. Finite cost_stable justifies stopping cost-focused investigation but does not resolve unknowns. Return a brief evidence-based outcome after stage_release succeeds."
            messages = [{"role": "user", "content": "Investigate this captured HOLDOUT release, select informative evidence tools, and stage the least-cost conservative repair. Explain unresolved claims."}]
            if api_mode == "chat_completions":
                messages.insert(0, {"role": "system", "content": instructions})
            async with httpx.AsyncClient(transport=transport, timeout=30.0, follow_redirects=False) as http:
                while True:
                    elapsed = time.monotonic() - started
                    if usage["provider_calls"] >= budget.provider_calls or usage["tokens"] >= budget.tokens or elapsed >= budget.seconds:
                        raise AgentBudgetExceeded("The live investigator reached its configured call, token or elapsed-time budget.")
                    # The evidence graph, not the transcript, is authoritative.
                    # Start a fresh bounded conversation when tool results make
                    # the previous stateless request too large. No prior verdict,
                    # content or graph node is erased by this context compaction.
                    context_size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
                    if len(messages) > 36 or context_size > 72000:
                        status = service.compact_status()
                        compact_context = {
                            "task": "Continue the HOLDOUT investigation from the authoritative FalkorDB state. Inspect status first; use MCP tools for any missing details. Stage a conservative verified release, then report its limits.",
                            "current_state": {key: status[key] for key in ("run_id", "revision", "counts", "bounds", "gap", "next_candidate", "release", "scope")},
                            "note": "Earlier tool outputs were compacted; do not assume that absent evidence is resolved. All evidence remains in the graph."
                        }
                        messages = [{"role":"user", "content": json.dumps(compact_context, ensure_ascii=False)}]
                        if api_mode == "chat_completions":
                            messages.insert(0, {"role":"system", "content": instructions})
                        usage["compactions"] += 1
                        service._event("context_compacted", f"Started a bounded model context from FalkorDB revision {status['revision']}.")
                    # A provider-neutral byte reservation intentionally errs on
                    # the side of stopping early. Provider-specific hidden
                    # accounting is not a billing guarantee.
                    request = {"model": model, "tools": schemas, "tool_choice": "auto"}
                    if api_mode == "responses":
                        request.update(input=messages, instructions=instructions, store=False)
                        if reasoning_effort:
                            request["reasoning"] = {"effort": reasoning_effort}
                    else:
                        request["messages"] = messages
                    reserved_input = len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + 64 * len(messages) + 256
                    remaining_output = budget.tokens - usage["tokens"] - reserved_input
                    if remaining_output < 1:
                        raise AgentBudgetExceeded("The next request does not fit the conservative input/output token reservation.")
                    request["max_output_tokens" if api_mode == "responses" else "max_completion_tokens"] = min(output_limit, remaining_output)
                    # Local context cap also applies when a provider omits usage.
                    if len(json.dumps(request)) > 250000:
                        raise AgentBudgetExceeded("The investigator context exceeded the local byte budget.")
                    response = await http.post(endpoint, json=request,
                                               headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
                                               timeout=min(30.0, budget.seconds - elapsed))
                    usage["provider_calls"] += 1
                    if response.status_code >= 400:
                        raise RuntimeError(f"Model endpoint returned HTTP {response.status_code}; check backend provider configuration.")
                    try:
                        payload = response.json()
                    except ValueError:
                        raise RuntimeError("Model endpoint returned an invalid JSON result.") from None
                    if not isinstance(payload, dict):
                        raise RuntimeError("Model endpoint returned a malformed result.")
                    provider_usage = payload.get("usage")
                    provider_tokens = provider_usage.get("total_tokens") if isinstance(provider_usage, dict) else None
                    if isinstance(provider_tokens, int) and not isinstance(provider_tokens, bool) and provider_tokens >= 0:
                        usage["tokens"] += provider_tokens
                    else:
                        usage["token_accounting"] = "conservative byte estimate where usage unavailable"
                        usage["tokens"] += reserved_input + len(response.content)
                    if usage["tokens"] > budget.tokens:
                        raise AgentBudgetExceeded("The model response consumed the remaining token budget; no further tool actions were taken.")
                    if api_mode == "responses":
                        output, calls, final_text = _responses_output(payload)
                        messages.extend(output)
                    else:
                        message = payload["choices"][0]["message"]
                        calls = [{"id": call["id"], "name": call.get("function", {}).get("name"),
                                  "arguments": call.get("function", {}).get("arguments", "{}")} for call in message.get("tool_calls", [])]
                        final_text = str(message.get("content") or "")
                        messages.append({key: message[key] for key in ("role", "content", "tool_calls") if key in message} | {"role": "assistant"})
                    if not calls:
                        verified = service.release and service.release["verified"]
                        if verified and (api_mode == "chat_completions" or final_text.strip()):
                            _status(service, status="completed", message=(final_text or "Verified conservative release staged.")[:2000], usage=usage)
                            if snapshot:
                                snapshot()
                            return {"release": service.release, "usage": usage}
                        followup = "A current verified release exists. Finish with a brief evidence-based outcome and disclose unresolved claims." if verified else "No current verified release exists. Continue with the tools, or explain infeasibility. A textual claim does not create a release."
                        messages.append({"role": "user", "content": followup})
                        continue
                    for call in calls:
                        if usage["tool_calls"] >= budget.tool_calls or time.monotonic() - started >= budget.seconds:
                            raise AgentBudgetExceeded("The investigator reached its tool or elapsed-time budget.")
                        name = call["name"]
                        usage["tool_calls"] += 1
                        try:
                            if name not in allowed:
                                raise ValueError("Unknown MCP tool.")
                            arguments = json.loads(call["arguments"])
                            if not isinstance(arguments, dict):
                                raise ValueError("Tool arguments must be a JSON object.")
                            result = result_dict(await client.call_tool(name, arguments))
                        except (ValueError, KeyError) as exc:
                            result = {"error": str(exc)[:1000]}
                        if api_mode == "responses":
                            messages.append({"type": "function_call_output", "call_id": call["id"], "output": _bounded_payload(result)})
                        else:
                            messages.append({"role": "tool", "tool_call_id": call["id"], "content": _bounded_payload(result)})
                        _status(service, steps=usage["tool_calls"], usage=dict(usage), message=f"Live investigator called {name} through MCP.")
                        if snapshot:
                            snapshot()
    except BaseException:
        _status(service, status="failed", usage=usage, message="Live investigation stopped before completion; review the run log and any already staged release.")
        raise


def run_agent(service: HoldoutService, mode: str = "deterministic", snapshot: Callable[[], None] | None = None) -> dict:
    if mode not in {"deterministic", "graph_guided", "coordinated", "llm"}:
        raise ValueError("Agent mode must be deterministic, graph_guided, coordinated or llm.")
    coroutine = (deterministic(service, snapshot) if mode == "deterministic" else
                 graph_guided(service, snapshot) if mode == "graph_guided" else
                 coordinated(service, snapshot) if mode == "coordinated" else
                 live_model(service, snapshot=snapshot))
    return asyncio.run(coroutine)
