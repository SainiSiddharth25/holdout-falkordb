# HOLDOUT v2 — Import and discover

HOLDOUT v2 adds a bounded, **offline** candidate-discovery stage before the existing FalkorDB evidence investigation and native max-flow repair. This is a new workflow, not a claim that the product now detects all contamination or reconstructs missing lineage.

## Quickest start: local web import

1. Run the `holdout-v2` Docker Compose project (see README) and open `http://127.0.0.1:8020`.
2. In **Import a dataset release**, choose training and evaluation `.jsonl` files. Plain `.json` arrays work in the browser too.
3. Select an approved preservation policy and click **Import & discover candidates**.
4. Check the automatically generated conflict hypotheses, then run either investigation mode.
5. Export a conservative verified repair. The importer does not send data to any AI provider; **live LLM investigation will send bounded source excerpts to the configured provider**. Use only datasets you have permission to transmit.

The browser is limited to 3 MiB per file (the JSON API request to 8 MiB). The CLI supports `.jsonl`, `.json` arrays, and `.csv`, up to 40 MiB input per file and 5,000 records per split. The generated graph has a maximum of 25,000 candidates. Practical graph/solver scaling needs separate measurement.

## Example input format

One JSON object per line:

```json
{"id":"task-1","title":"Parse window", "content":"def clip(x, lo, hi):\n    return max(lo, min(x, hi))\n", "source_id":"repo-commit-abc", "protected":false}
```

Required: `content` (alternatively `code` or `text`), with UTF-8 contents up to 256 KiB.

Optional: `id`, `title`, `source_id`, `parent_id`, `parent_ids`, `derived_from`, `protected` (boolean), `content_role`, `solution_symbols` (list of independently answer-bearing Python function names). The importer assigns new stable-in-this-snapshot `t000001` and `e000001` IDs to separate the original IDs across pools. The original ID remains in each record's metadata.

For CSV, quote multiline cells. `solution_symbols` can be a JSON array string or comma-separated names. Metadata references are treated as unverified declarations, **not signed captures**.

**Answer inference:** when a sample has one standalone top-level Python function (not named `test_*` or `_private`), the importer labels it an inferred answer. This can misclassify helpers; verify on your own domain data. Multiple-function modules and non-Python text require explicit `solution_symbols` for the structural comparison tool. No uploaded code is executed.

## Costs and protection

The user selects the policy before graph creation. Every record in a split gets the stated integer exclusion cost:

| Policy | Training | Evaluation | Meaning |
|---|---:|---:|---|
| `equal` | 1 | 1 | Minimize number of excluded records |
| `balanced` | 2 | 5 | Prefer preserving evaluation examples modestly |
| `eval_first` | 1 | 10 | Strong preference for preserving evaluation examples |
| `train_first` | 8 | 3 | Strong preference for preserving training examples |

These **illustrative weights do not measure model accuracy or empirical value**. No agent may change them during investigation. `protected: true` remains a hard constraint. If protected records cannot coexist, the conservative repair is infeasible. Different approved policies may produce different exact repairs.

## Automatic discovery

Candidate retrieval uses indexes for:

1. Exact full-record SHA-256.
2. Exact extracted Python function bytes.
3. Bounded alpha-normalized Python AST function fingerprints.
4. High-overlap five-token shingles (Jaccard at least 0.84; the inverted-index search is bounded).
5. Matching metadata-derived source/parent references.

The first four derive evidence from content, but **all newly proposed conflicts start `unresolved`** until the existing evidence tools inspect the records and FalkorDB paths. Similarity is never silently equated with proven answer leakage. Provenance references are connected through `DERIVED_FROM` edges without capture receipts, so they do **not** pass the verified-lineage requirement automatically. Missing metadata stays unknown rather than being invented.

False positives (shared scaffolding) and false negatives (semantic rewrites with no structural overlap) remain possible. This is bounded candidate generation, not exhaustive contamination detection.

## CLI usage

The importer itself uses Python standard-library plus project source and needs **no FalkorDB, model key or external network**. In a checkout with Python 3.10+:

```bash
python3 -m holdout prepare \
  --train examples/v2-import/train.jsonl \
  --eval examples/v2-import/eval.jsonl \
  --output .runtime/my-new-import \
  --policy eval_first \
  --title 'My release review'
```

`--output` must not exist already. It contains `dataset.json` and immutable copied record bytes under `records/` and can be passed to the normal backend:

```bash
python -m holdout serve --fixture .runtime/my-new-import --port 8020
```

To run with Docker, using the **web importer is simpler** because the uploaded records are staged into the container's persistent runtime volume. The local UI never uses a sample output as if it were live evidence.

## Security, privacy and limits

- The server must remain bound to localhost. The importer does not follow references, fetch URLs, execute code, or traverse uploaded paths.
- Use only public, synthetic, or otherwise authorized data and confirm your model provider is permitted to receive captured snippets before enabling `Run live agent`.
- Files and resulting graphs are retained in the local runtime volume. Reset creates another graph; it is **not a deletion or secure erasure action**.
- Importing a new release replaces the active UI session after the new graph is ready. Existing runtime history remains on disk for audit; export important files before switching.
- No automatic Hugging Face streaming, git history crawling, arbitrary semantic analysis, or incremental multi-release processing is implemented here.
- Candidate discovery is separate from the evidence verdicts and exactly solved constrained two-pool exclusion problem.
