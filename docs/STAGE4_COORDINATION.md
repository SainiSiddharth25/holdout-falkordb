# Stage 4 — FalkorDB multi-agent task coordination

**Version 0.7.0.** This is a **post-hackathon experimental build**, not an automatic replacement for the submitted code. Stage 4 extends Stage 3 evidence-bound memory with a shared task queue in FalkorDB.

## What it actually implements

- Every candidate maps to one `(:InvestigationTask)-[:FOR_CANDIDATE]->(:Candidate)` node in FalkorDB.
- There are **two cooperating deterministic MCP specialists**, not two concurrent LLM API sessions:
  1. **Code specialist**: `record_identity`, `exact`, and `normalized_ast` where applicable.
  2. **Provenance specialist**: bounded FalkorDB source lineage.
- A code worker obtains a **claim token with an expiry** before making changes. If the candidate is unresolved after exhausting applicable code methods, it **hands off** its task to the provenance worker. The provenance worker does not inherit any verdict and assesses its own evidence. Every actual candidate status derives solely from existing evidence routines.
- Task transitions happen in FalkorDB, not an in-memory Python queue. `claim_task` and `transition_task` use conditional, single-statement Cypher writes, and service mutation calls validate a live claim at both initiation and commit. The API holds a service-wide lock for locally initiated operations, so competing local workers cannot corrupt the same service state.
- A **stale token is rejected** after a lease expires, is replaced or finishes. `/api/state`, `/api/coordination`, and the activity UI never reveal claim tokens. A local MCP client receives its own claim token; this is **not** a public auth boundary.
- Coordination events show claims, handoffs and completion, while the Stage 3 semantic/episodic/procedural memories remain independently validated against evidence receipts.
- A conservative stage requires task completion in the coordinated runner and still passes the existing native max-flow + independent receipt verification. The deterministic and graph-guided baselines remain available unchanged.

## Graph schema and queries

```cypher
MATCH (c:Candidate)
MERGE (t:InvestigationTask {id:c.id})
ON CREATE SET t.state=CASE WHEN c.status='unresolved' THEN 'pending' ELSE 'done' END,
              t.role='code',t.owner='',t.token='',t.lease_until=0,
              t.attempts=0,t.handoffs=0
MERGE (t)-[:FOR_CANDIDATE]->(c);

MATCH (t:InvestigationTask)-[:FOR_CANDIDATE]->(c:Candidate)
WHERE t.role=$role AND c.status='unresolved' AND
      (t.state='pending' OR (t.state='leased' AND t.lease_until < $now))
WITH t ORDER BY t.id LIMIT 1
SET t.state='leased',t.owner=$owner,t.token=$token,
    t.lease_until=$until,t.attempts=t.attempts+1
RETURN t.id,t.role,t.attempts;
```

Completing/handoff also requires `t.token=$token AND t.lease_until >= $now`. All user-controlled data uses query parameters. The lease token is a fencing capability, not a general user authentication mechanism.

`ReleaseMeta.revision` is reserved for candidate evidence changes. Task queue transitions do **not** change the evidence revision or invalidate mathematical certificates. They are recorded separately as `Event` nodes.

## Crash/restart behavior

The Stage 0 checkpoint stores source fingerprints and replayable evidence receipts. When the service restarts, it **rebuilds the graph** and replays those receipts. Stage 4 then **reconstructs task assignments from replayed evidence**; it does **not** replay or trust an old claim token, assigned owner, outstanding lease, handoff transcript, or arbitrary model note. Incomplete work is available to claim again. This is recovery of validated work, **not exactly-once external execution**.

If the active run is interrupted, the two-worker mode may be restarted without resetting the snapshot. An expired claim cannot commit, and a new token can be issued for still-unfinished work. Fresh runs from imported data create an independent queue.

## Boundaries and next steps

- A single local release, single service process, and cooperative MCP specialist execution. We have not proven correctness under geographically distributed services or cross-process external graph writers. A production system needs graph-level uniqueness constraints, authenticated worker identities, resource quotas, durable distributed coordination, and explicit lease renewals for long evidence operations.
- Not a full multi-model orchestrator: **the live-model mode is still one LLM**. The Stage 4 cooperative baseline provides testable task-ownership and handoff semantics before adding multiple paid LLMs.
- Tasks can be retried; service mutations must be evidence-idempotent, source-revision-bound and checked. Checkpoint replay reconstructs the candidate verdict, not an externally executed side effect.
- Only candidate tasks in the **current imported snapshot** are coordinated. No company-team ownership, cross-release task scheduling, or tenant isolation is implemented yet. Those belong to later stages.
- The native FalkorDB+MCP acceptance tests must pass on Docker before promoting this preview.

## Local acceptance

```bash
cp .env.example .env
set -a; source .env; set +a
docker compose -p holdout-sentinel-s4 --env-file .env up -d --build
# Open http://127.0.0.1:8080, select Run coordinated agents.
# Inspect claims, handoffs, final release and evidence memory.
docker compose -p holdout-sentinel-s4 --env-file .env exec -T holdout \
  /app/.venv/bin/python -m pytest -q tests/test_stage4_native.py \
  tests/test_stage3_native.py tests/test_stage2_native.py \
  tests/test_stage11_native.py tests/test_checkpoint_native.py
```

No Requesty key is needed for the native test or coordinated baseline. Preserve the volume during restart testing; do not use `docker compose down -v`.
