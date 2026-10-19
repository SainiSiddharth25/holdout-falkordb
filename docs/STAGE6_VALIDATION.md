# Stage 6 validation — 2026-10-19

Stage 5 is the source baseline. This build adds local watched pair fingerprinting, change-impact projection over FalkorDB candidate endpoints, explicit digest-bound promotion, freshness guards, stale governance reporting, a source-watch UI, and Docker-native test cases.

**Offline result: 79 tests passed** (six new Stage 6 watcher/API tests and 73 selected inherited tests, test-only graph adapters for API flows). No provider calls.

Offline: `PYTHONPATH=tests:. python -m pytest -q tests/test_stage6_watch.py tests/test_stage5_company_brain.py tests/test_stage4_coordination.py tests/test_stage3_memory.py tests/test_stage2_planning.py tests/test_stage11_review.py tests/test_ingest.py tests/test_stage1_sources.py`.

Browser: `node --check dist/app.js` passed; six source-level DOM contract checks passed. Full browser-rendered QA was not run.

Native FalkorDB/MCP: acceptance tests included under `tests/test_stage6_native.py`; **not run in the authoring container without FalkorDB/MCP**. Run in Docker before accepting Stage 6 as production-ready.

This is a **single-process local preview**. Source deltas are identified by stable split/original ID hashes. Only direct incident candidates linked to changed records are counted as affected; this is not a full dependency invalidation engine or incremental solver.
