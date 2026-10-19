# Stage 1.1 validation — 2026-10-19

## Result and boundaries

**78 selected tests passed; 6 subtests passed; the native test module was skipped because FalkorDB is not installed.** The test environment ran Python 3.13.5. No Docker build, new native FalkorDB run, real MCP session, or live-provider run was completed for this version here.

The existing 34 importer/evidence/checkpoint/Git-source tests pass. New tests cover policy separation, classes and multi-function modules, explicit scaffolding, method applicability, immutable rules, same-snapshot idempotence, review counters, empty pools, evidence/manifest/policy/summary tampering, schema-1 compatibility, stored-sequence recovery and HTTP import contracts.

Twenty service-contract cases use the explicit `tests/stage11_support.py` adapter, which stores test state in memory and computes flows using NetworkX. The actual product solver's compilation/certificate checks and service/verification code run against that adapter, but this **does not test native max-flow or Cypher execution**. The real application does not import or fall back to the test adapter.

The original bundled schema-1 release replays against the original fixture. The legacy AST hash difference encountered on Python 3.13 was traced to empty-field formatting, not changed source bytes; retaining empty AST fields restores the existing Python 3.12 comparison hash for the supported example. Broader cross-version syntax equivalence is not asserted.

## Command actually run

```bash
python -m pytest -q \
  tests/test_evidence.py tests/test_ingest.py tests/test_checkpoint.py \
  tests/test_stage1_sources.py tests/test_stage11_review.py \
  tests/test_stage11_service_contracts.py tests/test_stage11_native.py
```

Python compilation and `node --check dist/app.js` passed. CLI `prepare` also ran on the new example with `--separation-policy exact_record`, producing 10 records and 5 candidates in a new immutable snapshot.

## Browser checks

Seven browser checks passed with zero JavaScript page errors. Chromium rendered the actual local HTML, CSS and JavaScript in memory, using a test-backed Fetch interface. No browser network navigation, native engine, MCP or model request was used.

- Live-agent primary button and secondary deterministic control preserved.
- Content identity is displayed separately from answer relevance.
- Unavailable methods have explanations; exhausted investigation is disabled.
- Per-split retention and coverage are rendered.
- Importing a new rule with the same record IDs does not reuse stale candidate detail.
- Empty-pool warning appears before staging.
- Mobile review at 390px has no horizontal overflow.

Developer-only rerun (requires separately installed Playwright and Chromium):

```bash
python scripts/ui_stage11_smoke.py
```

The UI helper and its screenshots are not live-run evidence. Runtime screenshots are excluded from the source package. The browser check report is in `stage11-browser-report.json`.

## Why native checks remain pending

Docker is unavailable in this environment. A single dependency-installation attempt failed due to DNS/network access, so FalkorDB and MCP packages could not be obtained. This is not a claim that the pinned versions are unavailable publicly. No unchanged installation retry loop or solver substitution was used to claim a native pass.

## Required promotion gate on WSL/Docker

```bash
docker compose -p holdout-sentinel-s11 --env-file .env up -d --build
docker compose -p holdout-sentinel-s11 --env-file .env exec -T holdout \
  /app/.venv/bin/python -m pytest -q tests/test_stage11_native.py tests/test_checkpoint_native.py
```

The new native tests run both policies through real MCP and FalkorDB, stage/verify, close/reopen, and replay a checkpoint. They make no provider calls. After they pass, check the browser manually with the new 10-record example and the existing copied Git snapshots.

Do not mark Stage 1.1 native-validated based on the earlier user-reported Stage 1 success. Model budget efficiency, full Git-history provenance, production memory/coordination, and Stage 2 active prioritization have not been established by this update.
