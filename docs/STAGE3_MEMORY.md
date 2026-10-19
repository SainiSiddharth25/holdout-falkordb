# HOLDOUT Sentinel Stage 3: evidence-bound persistent memory

Version 0.6.0, 2026-10-19. Extends Stage 2. This is a separate preview, not a replacement for the previously submitted hackathon build.

## Goal and bounded scope

HOLDOUT now retains structured investigation memory in **FalkorDB**. It does not create a second unverified LLM memory database. No agent can write a trusted verdict or memory fact directly. The deterministic evidence tools record verdicts first, and only then does the service derive three types of memory from each assessment receipt:

- **Episodic** — methods attempted in order, observed outputs, final bounded explanation, and revision when observed.
- **Semantic** — a strictly scoped statement: *candidate X had status Y under separation policy P according to receipt Z*. An `unresolved` status remains unresolved. This is not an independent contamination assertion.
- **Procedural** — a record of which methods were tried for what type of source and what happened; compatible methods may be *suggested* for a future claim. A past finding never transfers a verdict to a new claim.

All records contain candidate endpoints, the immutable fixture-catalog digest, separation policy, receipt digest, reason digest, and a deterministic memory identifier. Actual source and transformation content stays in the fixture; memory stores bounded derived summaries and source links, not full dataset copies.

## Graph data model and Cypher

```
(:Memory {id, kind, data})-[:ABOUT]->(:Candidate {id})
(:Memory)-[:SUPPORTED_BY]->(:EvidenceReceipt {id})
(:Candidate)-[:TRAIN]->(:Record)
(:Candidate)-[:EVAL]->(:Record)
```

The memory relationships are created by the application using `UNWIND`, `MATCH`, and `MERGE`. `GraphStore.memories()` uses Cypher `MATCH` and filters by candidate ID/kind. These nodes are part of the live FalkorDB evidence graph and do not modify the graph's **decision revision** or the native max-flow optimization.

At recall, the service validates the memory against **current** candidate endpoints, separation policy, source catalog fingerprint, deterministic assessment receipt, and recomputed expected content. An obsolete or forged memory is marked **stale** and suppressed from default recall. Source files are fingerprinted before memory retrieval; changed source bytes fail closed. Stale history can be viewed using an explicit flag for debugging, but is never returned as current.

The runtime checkpoint stores replayable candidate evidence decisions, *not* a serialized model transcript or trusted memory prose. On service restart, HOLDOUT rebuilds FalkorDB, reruns the exact original evidence methods, checks the stored receipts, and **reconstructs** semantic, episodic, and procedural memory from those revalidated results. Stable IDs allow comparing recovered memories across restarts. The memory graph is thus recovered from independently replayed evidence, rather than loaded as unchecked assertions.

### Explicit boundaries

- Stage 3 memory persists across **restarts of the same active release**. It is not yet a global memory pool spanning different dataset releases or unrelated tenants. That extension requires source-identity mapping, authorizations, and evidence revalidation across releases.
- Procedural reuse is **suggested**, not automatically executed. It does not imply cross-task correctness or validated performance improvement.
- `memory_overview` is a cached count for the current graph revision. Fetching `/api/memory` rechecks fixture bytes and expected notes; it is the authoritative read operation.
- No LLM-authored open-ended notes can be promoted into trusted semantic facts. This intentionally limits expressiveness until agent-memory provenance and approval mechanisms are available.
- The current agent memory functionality does **not** provide distributed task coordination, cross-agent leases, or tenant isolation. Those are Stage 4 capabilities.
- Full memory recall is bounded: MCP returns at most 60 records per call, API 100, storage query 500. Large research datasets will require pagination and retention policies.

## New tools and API

The existing eight domain MCP tools are retained. Two are added:

- `recall_memory(candidate_id?, kind?, limit=20)` — returns only current evidence-backed memories.
- `suggest_procedures(candidate_id,limit=5)` — returns compatible method-order hints from other verified investigations; does not execute tools or transfer verdicts.

The local API provides read-only `/api/memory` (kind, candidate, limit, include_stale) and `/api/memory/suggestions/{candidate_id}`. The Memory panel displays the counts and allows on-demand recall. The live agent instructions request memory lookup before planning a continuation. **Run live agent** remains first and primary; graph-guided and deterministic options remain available.

## Example of intended behavior

1. Import or load the controlled sensitivity example.
2. Investigate candidate `a-x` using `exact`.
3. Select **Recall evidence memories** in the Memory panel. Three current memory notes appear with the evidence receipt digest.
4. Fetch `/api/memory/suggestions/a-y`. The tool can suggest `exact` from prior experience but `a-y` is still unresolved.
5. Restart the service while preserving the Docker Compose volume. The checkpoint evidence is independently replayed; the same evidence-bound memory IDs are reconstructed.
6. If the candidate receipt, policy, or source bytes change, old memories are not accepted as current evidence.

No Requesty key is needed for these steps.

## Local Docker setup

Extract this version into a fresh folder, then from `holdout/`:

```bash
cp .env.example .env
# Add a rotated Requesty key locally only if testing the live agent.
set -a; source .env; set +a
docker compose -p holdout-sentinel-s3 --env-file .env up -d --build
```

Open <http://127.0.0.1:8070>. The name, host port and Compose volume are independent of Stage 2 (`8060`).

### Native promotion gate (no model calls)

```bash
docker compose -p holdout-sentinel-s3 --env-file .env exec -T holdout \
  /app/.venv/bin/python -m pytest -q \
  tests/test_stage3_native.py tests/test_stage2_native.py \
  tests/test_stage11_native.py tests/test_checkpoint_native.py
```

And restart check:

```bash
docker compose -p holdout-sentinel-s3 --env-file .env stop
docker compose -p holdout-sentinel-s3 --env-file .env up -d --no-build
```

Reopen Memory; the saved decisions and graph memories should reappear. Do **not** run `docker compose down -v` between restart steps; that deletes the checkpoint volume.

See `STAGE3_VALIDATION.md` for separate offline and native validation statuses.
