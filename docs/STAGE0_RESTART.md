# Sentinel Stage 0 — durable evidence restart preview

Stage 0 establishes a durable *single-active-run* recovery mechanism. This is **not yet** distributed task memory, multi-agent coordination, or the MCP Tasks extension.

## What it does

- The normal local HTTP app writes an atomic checkpoint to `.runtime/sessions/checkpoints/<run_id>.json` **after each completed candidate judgment and staged release**.
- An atomic `.runtime/sessions/active-session.json` points to the committed active run. Importing or resetting switches that pointer only after the new graph and checkpoint are prepared.
- On service startup, it opens the saved fixture snapshot, validates the catalog and all captured-file SHA-256 fingerprints, creates a fresh FalkorDB graph and **replays the saved methods through the deterministic evidence tool**. Each recovered status and semantic evidence digest must match.
- If a release had been staged, it is **recomputed and reverified**; it is not trusted because an old metadata file said `verified=true`.
- An interrupted agent can start a new model attempt using the restored graph. The prior provider transcript is *not* treated as trustworthy, resumed or billed again.
- These durable checkpoint files are not signed; independent source control and access controls remain essential.

## What it deliberately does NOT claim

- Not full per-call transcript persistence, distributed worker leases, safe cross-instance writes, multi-tenant isolation, or durable native MCP Tasks-extension handles.
- Not an agent-call token refund, nor a guarantee that a provider error is recoverable without a new model request.
- The original model's exact investigatory tool order and complete event timeline are not reconstructed. Restored verdicts are replayed against captured evidence; a new `session_resumed` event records the recovery.
- The checkpoint is local to the Compose volume. Removing that volume destroys these recovery records.

## Local WSL/Docker smoke test

Use **a fresh directory** and keep v2 running at port 8020 if needed. This preview uses **8030**.

```bash
cp .env.example .env
# Fill in credentials locally only if testing live-model mode.
set -a; source .env; set +a
docker compose -p holdout-sentinel --env-file .env up -d --build
```

At http://127.0.0.1:8030, import the v2 examples and investigate at least two candidates (or run the deterministic policy); note statuses/revision. Stop and restart the app **without removing volumes**:

```bash
docker compose -p holdout-sentinel --env-file .env stop
docker compose -p holdout-sentinel --env-file .env up -d --no-build
```

Refresh http://127.0.0.1:8030 and check that the statuses, exclusion bounds and verified staged artifacts remain consistent. Look for `session_resumed` in agent events. No live model key is needed for this test.

Then modify a *copy* of an imported source fixture (not the demo fixture in the repository), restart and confirm that the server refuses to accept the changed snapshot rather than silently reuse prior verdicts. Restore the unmodified fixture afterward.

Do **not** run `docker compose down -v` until you've exported the release and no longer need the checkpoint. For the native integration tests, use the pinned engine on Linux and run:

```bash
python -m pytest -q tests/test_checkpoint.py tests/test_checkpoint_native.py
```

## Failure handling

A corrupted pointer, changed source, or mismatch in replayed evidence fails startup closed. Preserve the checkpoint files for diagnosis rather than deleting them to make an invalid verdict appear to restore. Original receipts remain in their runtime directories; only current evidence and valid active release are returned to the UI.
