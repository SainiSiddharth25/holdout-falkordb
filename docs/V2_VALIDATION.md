# HOLDOUT v2 local validation record

## What was actually tested in the v2 authoring environment

- `python -m holdout prepare` successfully created a new `dataset.json` and record artifacts from the bundled real-file-format JSONL examples (5 records, 2 automatically discovered candidates).
- Import and evidence tests: **21 tests + 6 subtests passed** (`tests/test_evidence.py`, `tests/test_ingest.py`). This includes JSONL, JSON-object input, CSV, policy weights, protected records, fingerprint search, duplicate/invalid inputs, no overwritten snapshots, and no automatic promotion of metadata hints to verified lineage.
- Python syntax compilation succeeded for the new and modified Python modules.
- The review UI JavaScript passed `node --check`.
- Source archive is checked by SHA-256 manifest and ZIP CRC; its exact bytes are available for reproducibility.

## Not run in this environment

This isolated authoring container does **not** have the pinned FalkorDB native module, `falkordb` Python client, MCP SDK, or Docker engine. Therefore the integrated imported-data → FalkorDB → MCP → max-flow → verified release path, Docker build, and browser UI were not run here for v2. The earlier v1 synthetic-release run is not proof of v2 integration. These checks should be run on WSL/Docker before considering v2 release-ready:

```bash
cp .env.example .env
# Configure a permitted model only if you need live mode.
docker compose -p holdout-v2 --env-file .env up -d --build
# Open http://127.0.0.1:8020, import examples/v2-import JSONL files and
# run deterministic investigation, then stage and verify the release.
# Test the imported-data path in the UI; tests live in the source checkout,
# not in the production container image.
```

The Docker image does not copy `tests` or `examples` by default. Run the full pinned suite in a test-enabled dev build or a compatible local Python environment. Do not claim full v2 end-to-end validation until this is completed.

To test the shipped Docker image directly, use the UI import and check the completed release plus three artifact downloads. If that succeeds, the native algorithm, graph capture, and receipt flow are operating on imported data.
