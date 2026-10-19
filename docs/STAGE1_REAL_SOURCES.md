# Stage 1 — Real-source discovery (preview)

This extends the verified Stage 0 checkpoint system, without changing the original weighted bipartite minimum-cut problem. The source importer uses read-only offline operations and makes **no network requests**. Git metadata records version facts, not proof that a solution was preserved. Candidate discovery remains an incomplete retrieval stage: every new relationship starts **unresolved** until an evidence tool adjudicates it.

## Local web workflow

1. Extract this build into a new folder; leave the hackathon submission and Stage 0 working checkout untouched.
2. `cp .env.example .env`; set model credentials locally only when using live mode.
3. `docker compose -p holdout-sentinel-s1 --env-file .env up -d --build`.
4. Open `http://127.0.0.1:8040`. **Run live agent** is now the main call to action when a model provider is configured; **Run deterministic policy** remains the non-prominent baseline.
5. Upload two local JSONL datasets using the **Import** panel, select a preservation policy, investigate evidence, and stage a verified conservative repair.

The web interface accepts JSONL/JSON (up to 3 MiB per browser file). CSV and Parquet use the `prepare` CLI below. Local browser import replaces the active release after the new graph has been created and staged. Preserve existing release artifacts first. Reset and container deletion do not securely erase previously captured content.

## Capture an actual local Git repository

Use **an already checked-out public or authorized repository**. If needed, clone it yourself using your normal Git credentials and permissions; Sentinel does not clone or execute it.

In Linux, WSL, or within a Docker container that can access the repository and input/output paths:

```bash
python3 -m holdout git-snapshot \
  --repo /path/to/checked-out-repository \
  --revision HEAD \
  --pattern '*.py' \
  --history-depth 4 \
  --repo-label my-public-repo \
  --output ./train-git.jsonl

python3 -m holdout git-snapshot \
  --repo /path/to/checked-out-repository \
  --revision HEAD~1 \
  --pattern '*.py' \
  --history-depth 4 \
  --repo-label my-public-repo \
  --output ./eval-git.jsonl
```

The two-revision example is **a source-ingestion smoke test**, not a scientifically valid benchmark split. For meaningful contamination evaluation, construct independent candidate training/evaluation examples and appropriate ground truth rather than assuming historical code versions are independent.

`git-snapshot` reads only tracked regular Git blobs at the specified pinned commit. It ignores symlinks and submodules, skips empty/oversized/non-UTF-8 files, captures up to 12 versions from *same-path* Git history, and verifies each blob against its Git object ID. It never runs repository code, checks out branches, executes hooks, or downloads remote objects. Rename-following and sparse/missing history are not yet implemented.

For each row it writes `content`, `source_id`, plus `git_snapshot` describing the captured Git commit/path/blob and checked earlier versions. The importer rehashes each included blob, stores immutable captured bytes under `sources/`, and creates `captured_git_blob` entities with `DERIVED_FROM` relationships in FalkorDB. These relationships make history inspectable, but **do not satisfy the evidence tool's solution-preserving provenance proof** without the separate transformation receipts it requires. Commit-tree membership was checked while reading the local repository; it is not independently authenticated from exported JSONL alone.

You can upload small generated Git JSONL files through the browser importer. For larger files use `prepare` directly:

```bash
python3 -m holdout prepare \
  --train train-git.jsonl --eval eval-git.jsonl \
  --output .runtime/imports/my-git-run --policy equal
```

To run an imported fixture directly, launch the backend with `python -m holdout serve --fixture .runtime/imports/my-git-run` from a compatible Python/native-engine environment. In Docker, mount inputs/fixtures into the container and run the corresponding command there; the browser cannot open arbitrary host paths by itself.

## Parquet, JSONL, JSON and CSV

`holdout prepare` accepts `.jsonl`, `.json` arrays, `.csv`, and **`.parquet`**. Parquet decoding uses `pyarrow==21.0.0` (included in the Docker image; optional `pip install pyarrow==21.0.0` for host-only preparation). Parquet records must provide a scalar `content`, `code`, or `text` field. Additional metadata can include `id`, `source_id`, `parent_id`, `parent_ids`, `derived_from`, `protected`, and optional `solution_symbols`.

```bash
python3 -m holdout prepare \
  --train ./data/train.parquet \
  --eval ./data/eval.parquet \
  --output ./imports/parquet-trial \
  --policy balanced
```

Resource bounds: input files ≤40 MiB each; Parquet row-group uncompressed metadata ≤80 MiB; ≤5,000 records per split; ≤256 KiB per record; ≤25,000 candidate edges. Git snapshot defaults to ≤250 tracked matching files; snapshots must fit ≤40 MiB JSONL. These are bounded development limits, not large-scale processing guarantees.

## Public Hugging Face data

Public Hugging Face datasets are currently supported **through locally downloaded/converted JSONL, CSV or Parquet files**. For example, use Hugging Face's official Hub UI or `hf download` CLI to fetch an allowed public dataset's actual files, review its license and fields, then point `holdout prepare` at those files. For gated, personal, paywalled or restricted data, obtain explicit permission and ensure your chosen model provider may receive snippets before running a live agent.

There is **no built-in automatic Hub streaming or arbitrary URL ingestion** in this stage. This avoids silently downloading restricted content and avoids outbound URL security problems. Direct Hub connector, permission policies, and streaming are later work.

## Candidate generation and evidence

Existing indexed retrieval signals are full-record SHA-256, exact answer-function SHA-256, bounded normalized Python AST, token-shingle similarity, and shared source metadata. New Git file identity (`gitfile:<repo-label>:<path>`) contributes source-reference candidates between versions even when the bytes differ. The import summary reports a count for each retrieval signal and the total number of possible cross-pool pairs. Those are **candidate counts, not ground-truth precision or recall**.

Every discovered candidate is **unresolved**. A same-file history edge, a matching Git blob, a source ID, or a similarity score is **not automatically a confirmed leak**. Evidence tools only confirm conflicts where the configured deterministic comparison establishes answer overlap. An unresolved candidate is included in the conservative repair; rejected candidates are not.

The current automatic Python answer-symbol inference for standalone functions is a heuristic and may mislabel helpers. Human reviewers should declare `solution_symbols` when using real benchmark material. No imported code is ever executed by the evidence tools.

## Outstanding Stage 1 gates

- End-to-end real native-engine Git import → MCP investigation → stage/export → restart/replay on WSL Docker.
- Run actual Parquet decoding under `pyarrow` in Docker; unit tests currently use a bounded fake reader due local dependency unavailability.
- Independently labeled candidate retrieval precision/recall on real, permitted data; index performance and resource usage for larger inputs.
- Direct GitHub and Hugging Face API connectors, rename-aware history, verified external provenance receipts and incremental dataset updates.

See `docs/STAGE1_VALIDATION.md` for what was tested and what remains unverified.
