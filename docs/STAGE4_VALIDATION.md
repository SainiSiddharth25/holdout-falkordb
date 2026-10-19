# Stage 4 validation — separate evidence levels

## Completed checks in this authoring environment (2026-10-19)

- **116 offline tests and 6 subtests passed**, including Stage 4 fencing/claim tests, Stage 3 receipt-bound memory tests, Stage 2 planning tests, Stage 1 import/evidence tests, checkpoint reconstruction, and earlier review/receipt checks. Stage 4 tests exercise a **test-only NetworkX solver and in-memory graph task adapter**, not native FalkorDB.
- Python source compilation and JavaScript syntax checks pass.
- Static HTML control existence and control wiring were inspected.
- A unique source archive was built and re-read to compare SHA-256 hashes against the manifest.

## Not performed here

The environment has **no Docker daemon and no installed FalkorDB/MCP packages**. Therefore actual native FalkorDB Cypher task transitions, concurrent MCP sessions, native certificate verification, and restart recovery under the packaged container **have not been tested here**. The real end-to-end claim is contingent on the native gate below; do not call Stage 4 production-validated without it.

## Native promotion gate

From a fresh extraction inside `holdout`:

```bash
cp .env.example .env
set -a; source .env; set +a
docker compose -p holdout-sentinel-s4 --env-file .env up -d --build

docker compose -p holdout-sentinel-s4 --env-file .env exec -T holdout \
  /app/.venv/bin/python -m pytest -q \
  tests/test_stage4_native.py \
  tests/test_stage3_native.py \
  tests/test_stage2_native.py \
  tests/test_stage11_native.py \
  tests/test_checkpoint_native.py
```

Expected acceptance topics: atomic exclusive claims, role-specific investigations and handoffs over real SDK MCP, stale lease-token rejection, verified conservative release with max-flow certificate, valid memory recall, restoration that invalidates old tokens, graph planning correctness.

## Manual smoke test

1. Visit `http://127.0.0.1:8080` and load the **sensitivity example**.
2. Click **Run coordinated agents** (no Requesty key required).
3. Check activity for `task_claimed`, `task_handoff`, `task_completed`, `solve_release`, and `stage_release`.
4. In **Two specialists, one evidence graph**, verify zero leased/waiting tasks at completion, plus role handoffs where appropriate. Observed unresolved claims must remain unresolved rather than being assigned a guessed status.
5. Download and independently verify the release artifacts. A valid conservative receipt is required; an LLM-generated summary alone is not sufficient.
6. Stop/start the Compose service **without deleting the volume**, and verify that checked verdicts/memories are restored and old task leases are not renewed.

## Known limits

- Stage 4 workers are deterministic cooperative MCP clients, not two paid LLMs.
- No verified cross-process distributed coordination or tenant permission system.
- A claim token is a local task fence; it is not sufficient access control for publicly exposed MCP.
- The current native gate checks concurrent claims in one service process, not multi-host serializability.
