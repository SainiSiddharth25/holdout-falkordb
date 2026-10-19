# HOLDOUT Sentinel — Stage 1 validation

**Date:** 2026-10-19. **Status:** offline Stage 1 source preview, pending native Docker smoke tests.

## Evidence from local execution

- A real temporary two-commit Git repository was snapshotted through the `holdout git-snapshot` CLI. Both revisions were converted to JSONL and imported into a HOLDOUT fixture.
- The imported fixture contained captured Git blob source nodes, history relationships, and an unresolved same-file source-reference candidate. Git blob bytes and hashes were checked again on import. No Git history was misrepresented as verified solution-preserving lineage.
- Unit tests cover symlink omission, file/revision limits, an existing output refusal, invalid Git metadata hashes, malformed candidate imports, safe file paths, local content matching, and Stage 0 checkpoint behaviors.
- A Parquet batch-reader test used a stub; missing-dependency behavior was also checked. Actual pyarrow and Docker build were not available in this environment.
- Frontend source tests verify that the live-agent button is primary, the deterministic button is secondary, and live appears first in the DOM.

## Tests

**Local result: 34 passed, 6 subtests passed** across Git capture/import, importer, evidence checks, and Stage 0 checkpoint units. Python compilation and JavaScript syntax validation also passed. The full MCP/native suite could not run here because the container lacks FalkorDB/MCP runtime dependencies and Docker.


Run locally before publishing:

```bash
PYTHONPATH=. python3 -m pytest -q \
  tests/test_stage1_sources.py tests/test_ingest.py \
  tests/test_evidence.py tests/test_checkpoint.py
python3 -m compileall -q holdout
node --check dist/app.js
```

For the real native-engine integration:

```bash
docker compose -p holdout-sentinel-s1 --env-file .env up -d --build
# Use the UI at http://127.0.0.1:8040
# Import files, investigate, stage release, restart without deleting the volume
# Confirm the same evidence and release after restart
```

## Claims we are not making

There is no measured candidate recall/precision on a large external benchmark, no automatic URL/API integration, no GitHub organization permission handling, no live-model evaluation in this Stage 1 environment, no new solver-performance claim, and no guarantee against undetected contamination. A source artifact's Git object hash verifies its bytes, but cannot prove that two derived solutions are equivalent. The exact optimizer remains valid only under the existing two-pool additive-cost constraints.
