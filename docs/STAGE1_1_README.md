# HOLDOUT Sentinel — Stage 1.1

**Keep the signal. Fix the split. Know what the repair actually guarantees.**

This development build adds evidence-policy selection, method applicability and per-split release review to the working Stage 1 Git/JSONL importer. It keeps the FalkorDB-native optimizer, MCP tools, live-agent primary button, bounded model context, and source-checked checkpoint recovery.

## Start separately from Stage 1

Extract into a fresh folder. From its `holdout` directory:

```bash
cp .env.example .env
# Optional: edit OPENAI_API_KEY locally for a live Requesty run.
# Leave it blank for deterministic checks. Never commit .env.
set -a
source .env
set +a
docker compose -p holdout-sentinel-s11 --env-file .env up -d --build
```

Open **http://127.0.0.1:8050**. The separate Compose project and named volume leave Stage 1 on port 8040 unaffected. The Dockerfile supplies Ubuntu 24.04 and the existing pinned dependencies. Do not run the application with an unconfigured WSL Python 3.10 installation.

This archive does not contain credentials, a virtual environment, downloaded native binaries, or your previous investigation state. Reimport your existing JSONL snapshots; do not copy an old runtime directory into the new checkout.

## What changed

| Feature | Behavior |
|---|---|
| Conflict policy | Answer-leakage separation (default) or exact-record separation, fixed at import. |
| Whole-record observation | Complete-content equality is verified from captured bytes, separately from answer relevance. |
| Applicability | Unavailable and exhausted evidence methods are explained and skipped by auto investigation. |
| Release effects | Retained/excluded counts per split, coverage by verdict, unresolved reasons, and empty-pool warnings. |
| Receipts | Schema 2 binds the rule and verifies the review; schema 1 reading remains supported. |
| Recovery | Replays the actual recorded method sequence against unchanged evidence, including new identity checks. |

An identical utility module can be a verified duplicate yet remain unresolved **as answer leakage**. Exact-record policy forbids identical complete contents regardless of answer role; it does not promise to detect renamed or partial answer overlap. Costs from the two policies are not like-for-like quality comparisons.

## First test

Import `examples/stage1_1-review/train.jsonl` and `eval.jsonl`, using Equal costs.

| Selected rule | Confirmed | Rejected | Unresolved | Conservative cost |
|---|---:|---:|---:|---:|
| Answer-leakage | 1 | 0 | 4 | 5 |
| Exact-record | 3 | 2 | 0 | 3 |

These expected outcomes passed offline evidence/service tests. The native/MCP acceptance tests are supplied for your Docker runtime:

```bash
docker compose -p holdout-sentinel-s11 --env-file .env exec -T holdout \
  /app/.venv/bin/python -m pytest -q tests/test_stage11_native.py tests/test_checkpoint_native.py
```

To check restart behavior, stop and start this Compose project without removing its volume. Existing decisions must replay and a staged release must be verified again.

## Inputs and modes

Import JSONL/JSON through the browser. The CLI also supports CSV and optional Parquet. `git-snapshot` reads a local committed Git checkout without cloning or executing its code. Source metadata is a hypothesis; captured Git blob identity is not proof of solution-preserving transformations.

Preservation weights (`--policy`) and conflict semantics (`--separation-policy`) are independent:

```bash
python3 -m holdout prepare --train train.jsonl --eval eval.jsonl \
  --output new-snapshot --policy equal --separation-policy exact_record
```

The prepare command does not need a model or database. The live web/backend application requires the native runtime. It can investigate through a deterministic MCP policy or the configured LLM. Model limits and credentials remain operator-controlled in `.env`; record content may be sent to the configured provider in live mode. The UI makes the live-agent button primary when configured.

## Graph data model and algorithm

FalkorDB stores `Entity:Record` nodes, candidate nodes connected via `TRAIN` / `EVAL`, captured `DERIVED_FROM`, `CONTAINS`, and `USES_SCAFFOLD` relationships, plus revision and event state. `ReleaseMeta.separation_policy` records the active conflict rule. Record bytes and catalogs remain immutable artifacts; their hashes bind decisions and releases.

Evidence methods change candidate verdicts. The conservative optimizer covers confirmed and unresolved candidates. It compiles a weighted bipartite vertex-cover problem to source → training → evaluation → sink capacities, calls native `algo.maxFlow`, reconstructs the residual cut, and independently checks the certificate. The reference solver is an explicit benchmark, never a production fallback.

MCP tools remain: `release_status`, `list_candidates`, `inspect_record`, `trace_provenance`, `investigate_candidate`, `solve_release`, `stage_release`. `record_identity` is an added evidence method, not arbitrary code/query access. The agent cannot select policy changes, declare a verdict, or bypass verification.

Exports are `train.jsonl`, `eval.jsonl`, and `repair.json`. A verified optimum can still empty one pool: balance and minimum sizes are not constraints, and the UI now warns about that explicitly. A warning does not block export or create organizational approval.

## Documentation

- [Stage 1.1 behavior, compatibility and acceptance](docs/STAGE1_1_EVIDENCE_REVIEW.md)
- [What was and was not validated in this build](docs/STAGE1_1_VALIDATION.md)
- [Git snapshot and Parquet details](docs/STAGE1_REAL_SOURCES.md)
- [Architecture](docs/architecture.md) and [Cypher queries](docs/queries.cypher)
- [Live-provider settings](docs/live-agent-setup.md)
- [Sentinel roadmap](docs/SENTINEL_ROADMAP.md)

Stage 2 active investigation is not included yet; a controlled decision-impact fixture is provided for its future regression tests. Multi-agent memory/coordination and company ownership/approvals are still planned. This is a post-submission development snapshot, not a claim that all three tracks are complete.

## Validation boundaries

See the current validation report for exact commands and counts. Native FalkorDB/MCP packages could not be installed in this execution environment, and Docker is unavailable here. Offline service tests use a clearly labelled test-only graph/NetworkX adapter; browser tests render local assets with a test backend. Neither constitutes a native run or live-model validation. The production source continues to require the native algorithm.

Your earlier Stage 1 Docker run is separate user-reported evidence; Stage 1.1 needs its own native integration check before promotion.
