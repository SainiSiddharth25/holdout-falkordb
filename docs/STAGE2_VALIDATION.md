# Stage 2 validation — 2026-10-19

## Completed here

**106 selected tests and 6 subtests passed. Two native test modules were skipped because FalkorDB is not installed in this environment.** The run used Python 3.13.5. No Docker build, native max-flow invocation, real SDK MCP session or live-model request was completed here for this version.

```bash
python -m pytest -q \
  tests/test_evidence.py tests/test_ingest.py tests/test_checkpoint.py \
  tests/test_stage1_sources.py tests/test_stage11_review.py \
  tests/test_stage11_service_contracts.py tests/test_stage2_planning.py \
  tests/test_stage2_contracts.py tests/test_stage2_native.py \
  tests/test_stage11_native.py --junitxml=docs/stage2-tests.xml
```

The 28 new Stage 2 cases cover read-only scenarios, 11→3 sensitivity, protected-endpoint infeasibility, zero marginals with joint uncertainty, duplicate claims, stale revisions, immutable source checks, bounded scans/report payloads, method exhaustion, finite-bound stopping, report/certificate tampering, cached polling, real-evidence invalidation, checkpoint replay, guided and legacy runner contracts, HTTP routes/origin checks, and a paired baseline with identical stopping rules. Forty-five randomly generated six-record graphs have their single-claim scenarios checked against exhaustive exclusion-subset enumeration (part of one test, not 45 independent product benchmarks).

Service/optimizer tests explicitly use the **TEST-ONLY NetworkX adapter** in `tests/stage11_support.py`. Runner tests use a separately labelled local-client test double, not SDK MCP. No production module imports either adapter and the real solver never falls back to NetworkX. FastAPI request tests exercise the real HTTP application against that test service. Native promotion tests have no adapter or fallback.

Python compilation and `node --check dist/app.js` pass. The source archive is separately tested for ZIP integrity and per-file SHA-256 manifest consistency; clean-extraction CLI prepare is checked without a native database.

## Browser checks

Eight new Stage 2 browser checks pass with zero JavaScript page errors: primary live button, no fake precomputed plan, certified-scenario columns, partial-scan disclosure, invalidation after an evidence action, finite equality with unknowns still visible, guided-run controls/release review, and a 390px viewport without page-wide horizontal overflow. Seven inherited Stage 1.1 browser checks also pass.

These run Chromium against the actual HTML/CSS/JavaScript with an explicit test Fetch binding to the test service. They do not run a real HTTP server, native engine, SDK MCP or model. Desktop/mobile screenshots are developer visual QA, not live-execution evidence, and remain outside the source archive.

```bash
python scripts/ui_stage2_smoke.py
python scripts/ui_stage11_smoke.py
```

Requires developer-installed Playwright/Chromium, not included in normal Docker dependencies. Browser reports are `stage2-browser-report.json` and the historical Stage 1.1 report.

## Controlled comparison actually run

`scripts/benchmark_stage2.py --offline-contract` was executed with the explicit test adapter. `stage2-offline-benchmark.json` records the complete inputs/strategy naming, model/MCP flags, useful costs, action sequences, scenario counts and measured wall time.

After identical supported-evidence priming, with **one remaining candidate batch**:

| Controlled fixture | Graph-guided | Endpoint + same gap stop | Catalogue + same gap stop |
|---|---|---|---|
| Original sensitivity | Investigates high; cost 11→3 | Same choice and cost | Same choice and cost |
| Added expensive, zero-cost-impact claim | Investigates high; cost 31→23 | Investigates e-w; cost remains 31 | Investigates e-w; cost remains 31 |

With two batches the latter baselines also reach cost 23. This shows a particular weakness of the endpoint proxy, not a universal improvement. Graph-guided performs six extra scenario solve requests in the distractor comparison; the measured test-adapter wall time does not establish a runtime speed advantage. Both final costs are conservative and independently verified for their resulting evidence states.

The benchmark uses direct service calls, not MCP or an LLM. No model-token/credit saving or real-corpus detection quality was measured. The original fixture deliberately shows **no ranking advantage** over sensible baselines.

## Why native validation is pending

Docker is absent. One bounded dependency-installation attempt could not obtain `falkordb==1.8.0` from the package source available to this runtime. FalkorDB, FalkorDBLite and MCP are not installed here. This does not establish that those pinned versions are unavailable in the user's working environment or public package indexes. No repeated unchanged install loop, different production dependency stack, or test adapter was used to claim native success.

The Stage 1 Git smoke test reported by the user does not validate new Stage 2 native paths.

## Required WSL/Docker promotion gate

```bash
docker compose -p holdout-sentinel-s2 --env-file .env up -d --build
docker compose -p holdout-sentinel-s2 --env-file .env exec -T holdout \
  /app/.venv/bin/python -m pytest -q \
  tests/test_stage2_native.py tests/test_stage11_native.py tests/test_checkpoint_native.py
```

The image includes these tests and fixtures. The Stage 2 native cases use actual FalkorDB and SDK MCP to prove scenario/read-only separation, compare the controlled rank against the endpoint baseline, stage a conservative release, and replay recovery. The test command makes no Requesty requests. Skips or dependency failures do not count as native acceptance.

Then manually check the local page at http://127.0.0.1:8060 with the sensitivity example and an imported pair. Finally run one explicitly authorized live Requesty investigation to validate the new eighth tool and its use by the actual model. Keep the prior submitted tag and working Stage 1.1 build unchanged.
