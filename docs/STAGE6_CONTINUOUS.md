# Stage 6: Continuous Sentinel — monitored snapshots

## What works

- A daemon thread observes a **fixed, locally mounted** pair: `watch-inputs/train.jsonl` and `watch-inputs/eval.jsonl`.
- The watcher never executes imported code, follows URLs, or sends source content to a model. It rejects symlinks, invalid JSONL, duplicate IDs, >5,000 rows per split, and files >4 MiB each.
- The watcher keeps compact, bounded hash-and-event metadata in the persistent Compose volume (`.runtime/sessions/sentinel-watch.json`). Raw source records are not copied into this watch log.
- The UI displays pending source changes, added/removed/modified stable record keys, and counts the currently affected candidate relationships using FalkorDB's active record/candidate data. Only 60 changed keys and 60 candidate IDs are returned to the interface; counts are complete.
- Promoting requires the exact observed digest and active run ID, plus explicit preservation and conflict policies. It builds and validates a new graph before switching the active run; old releases remain available as historical files in the session volume. **No old verdict, procedural memory, or synthetic approval is transferred as trusted evidence to the new run.**
- A watched active run fails closed on source changes or missing watched files. Before any new investigation, conservative export, artifact download or synthetic review, freshness is checked immediately, not merely on a 15-second background interval.
- A former verified receipt remains mathematically valid for its original *captured version*. The UI marks its bound run as stale for current watched inputs and makes the organization policy gate non-publishable.

## How to demonstrate

1. In a fresh Stage 6 folder, copy `.env.example` to `.env`, leave model key empty, and run `docker compose -p holdout-sentinel-s6 --env-file .env up -d --build`.
2. Open `http://127.0.0.1:8100`. Go to **Sentinel · Source change watch**. The included watched files are a standalone small synthetic pair, independent of the initial fixture.
3. Choose the **Equal** cost policy and either explicit conflict policy in the import controls. In Sentinel, click **Check sources now**, then **Promote reviewed snapshot**. This creates a new imported graph and a new run ID.
4. Run **Run graph-guided** (no model API), then stage a verified conservative repair. Inspect the Company Brain policy gate if desired.
5. On the host, change one `content` string in `watch-inputs/train.jsonl` while retaining that record's `id`, using a temporary file and atomic `mv` if possible. Do not change `.env` or package files.
6. Click **Check sources now** (or allow the 15-second polling interval). The panel shows one modified record and the affected graph candidate count. The active run shows **source changed**; a release or approval attempt is blocked.
7. Click **Promote reviewed snapshot** again. The digest is checked, a new run is created, and no previous verdict or approval is silently inherited. Investigate and stage the new release. Restart Compose and confirm the promoted baseline is still recognized.

## Deliberate limitations

- **No autonomous repair or approval**: monitoring creates a proposed source change, not a trusted result. An operator must explicitly promote it, re-run evidence tools and stage a new release.
- **No incremental graph write on change**: Stage 6 finds directly affected existing conflict endpoints but rebuilds the candidate graph upon promotion. A future stage can safely replay unchanged evidence after independently checking it.
- **No external polling**: GitHub, Hugging Face and pipeline integrations still require a separate authorized source connector. Only the fixed local two-file pair is monitored.
- **No atomic multi-file producer handshake**: inputs are digest-bound and re-read before promotion, but changing both watched files in separate writes may expose an intermediate pair. Update both files while no promotion is occurring and publish through an atomic staging protocol for production use.
- **No secure enterprise approvals**: the Company Brain demo reviewer identity is synthetic. The source watcher is a local, single-process preview, not a distributed change-feed service.
- This system only handles the captured candidate graph, two fixed split assignments and additive exclusion costs. It cannot certify the absence of undiscovered contamination or improve an already-trained model.

## Native acceptance gate

Run `tests/test_stage6_native.py` together with `tests/test_stage5_native.py`, `tests/test_stage4_native.py`, `tests/test_stage3_native.py`, `tests/test_stage2_native.py`, `tests/test_stage11_native.py` and `tests/test_checkpoint_native.py` **inside the Docker runtime**. These use native FalkorDB/MCP and do not contact the LLM provider. Offline tests use an explicitly labelled graph adapter; they cannot establish native execution.
