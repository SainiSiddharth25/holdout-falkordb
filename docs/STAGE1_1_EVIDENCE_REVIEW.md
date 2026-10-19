# Sentinel Stage 1.1 — evidence, policy, and release review

This update builds on the working Stage 1 source archive. It is a separate development build, not a replacement of the submitted hackathon release. The exact native optimizer is unchanged. Stage 2 active planning, multi-agent coordination, and organization approvals are not implemented here.

## Two independent choices at import

**Preservation policy** (`policy`) still assigns fixed exclusion weights: equal, balanced, evaluation-first, or training-first. It is not a prediction of model accuracy.

**Conflict policy** (`separation_policy`) now selects what qualifies as a conflict:

| Identifier | Meaning | Nonidentical records |
|---|---|---|
| `answer_leakage` (default) | Existing bounded answer-function and verified solution-lineage rules | A mismatch alone does not establish independence. |
| `exact_record` | Prohibit byte-identical complete captured contents across the pools | A completed identity check rejects the pair under this exact-only rule. It does not establish answer independence. |

The second policy is deliberately narrower. A renamed answer may be confirmed under answer-leakage policy but rejected as outside exact-record policy. Do not compare their costs as if they provide the same protection.

Both choices are captured in a new immutable import. Changing a dropdown does not edit the active release. The model has no policy-changing tool. The active conflict rule is displayed separately from the next import controls.

Legacy datasets without the new field retain `answer_leakage` semantics; their catalogs are not silently rewritten.

## Three evidence layers

Candidate detail now presents:

1. **Observed identity:** byte equality plus independently recomputed hashes of complete captured contents. IDs, titles, and source metadata do not count as content identity.
2. **Answer relevance:** declared or importer-inferred answer functions, or an explicit explanation of their absence. Inference remains a reviewable heuristic, not independent proof of answer relevance.
3. **Policy verdict and release implication:** which rule is active, and whether conservative export must cover the pair.

A whole identical class or utility module can therefore show verified identity but an unresolved answer-level verdict. Shared scaffolding is not silently promoted to answer leakage.

`record_identity` is a new deterministic evidence method within the existing `investigate_candidate` MCP tool, not a new unbounded tool. Exact-record policy uses this method only. Answer-leakage policy can use it as an observation before its existing bounded checks.

## Method applicability

The inspector and MCP candidate summaries expose `available`, `unavailable`, and `exhausted` states with reasons. Auto investigation skips unavailable methods and already attempted methods. Explicit unsupported calls return an explanation without changing the candidate. Repeating an exhausted method on the same immutable input is idempotent.

Exact matching needs designated answer functions. Normalized matching additionally needs supported syntax. A provenance applicability check can notice that a record has no outgoing captured links, but it does not discover ancestry or infer a lineage verdict. Actual provenance paths continue to come from FalkorDB.

Missing Git transformation receipts still cannot establish preserved solution lineage. This update improves explanation and avoids known-unproductive checks; it does not add Git rename tracking or semantic equivalence.

## Release review, including bad-but-optimal outcomes

The conservative preview and staged release now show input, retained, excluded, exclusion cost, and protected-retained counts for each split. They also show confirmed/unresolved coverage and expandable reasons for unresolved claims.

An empty training or evaluation pool produces an explicit usability warning before export and afterwards. This is **a warning, not a new constraint or approval gate**. Minimum size, balance, category quotas and dataset quality remain outside the exact two-pool additive-cost optimization.

Equal costs can have many optimal repairs. Tests check cost, feasibility and certified counts, not a preferred tie-breaking allocation.

## Export and recovery contracts

New `repair.json` files use schema 2 and include the selected policy and a recomputable `review`. Verification rejects altered counts, coverage, warnings, method evidence and explanations. Original-evidence verification also binds the rule to the independently supplied catalog and content fingerprints.

Schema 1 verification remains available; the original bundled release still replays. These are consistency/evidence checks, not digital signatures.

Checkpoint format remains version 1 with `record_identity` admitted as a replayable method. Restoration replays the recorded method sequence rather than rerunning a potentially changed automatic policy. New calls preserve earlier attempts, and changed source/policy fingerprints fail closed. Old Docker/Python 3.12 AST comparison hashes are preserved on this Python 3.13 test runtime by explicitly including empty AST fields. This is not a guarantee of cross-version support for arbitrary future Python syntax.

Run the new build with its own Compose project and volume. Do not point an older binary at a new schema-2 release or copy runtime directories between checkouts.

## Small example for both policies

Upload these files through the browser:

- `examples/stage1_1-review/train.jsonl`
- `examples/stage1_1-review/eval.jsonl`

Choose Equal costs. Import and run deterministic investigation separately for each conflict policy. Expected outcomes from bounded evidence tests and the offline service-contract harness are:

| Policy | Records | Candidates | Confirmed | Rejected | Unresolved | Conservative cost |
|---|---:|---:|---:|---:|---:|---:|
| Answer-leakage | 10 | 5 | 1 | 0 | 4 | 5 |
| Exact-record | 10 | 5 | 3 | 2 | 0 | 3 |

These are different rules on the same data, not evidence that the second rule detects more answer leakage. Native-engine/MCP acceptance for these outcomes is included in `tests/test_stage11_native.py` and must run in the Docker environment.

For your copied 35-file Git snapshot, reuse both existing JSONL files. Exact-record policy should confirm every byte-identical pair, including class-only and multi-function files. Counts of additional pairs depend on the data. With Equal weights and one copy per side, the 35 matched pairs still force minimum cost 35; the improved display makes split-specific retention visible.

## Stage 2 regression foundation, not Stage 2 implementation

`fixtures/decision-impact/` is a self-authored controlled fixture. After confirming the three supported answer matches, its conservative cost is 11 and its confirmed-only optimum is 3. Resolving the shared-scaffold candidate as rejected lowers the conservative cost to 3. Rejecting the separate redundant candidate does not change cost 11.

The counterfactual values are checked by exhaustive subset enumeration in the tests, independent of the native flow operation. This does not establish real-data detection accuracy or implement an active-priority engine. It gives the next stage a concrete test where not every investigation is equally valuable.

## Commands

```bash
# From a new extracted holdout directory:
cp .env.example .env
# A key is optional. Do not print or commit your local .env.
docker compose -p holdout-sentinel-s11 --env-file .env up -d --build
# Browser: http://127.0.0.1:8050
```

A previous exported `HOLDOUT_HOST_PORT` shell value can override the file; the selected host port can be checked in your shell without displaying secrets. Either unset that variable or choose an unused port in `.env` and explicitly load it before creating the container.

For CLI import:

```bash
python3 -m holdout prepare --train train.jsonl --eval eval.jsonl \
  --output new-captured-release --policy equal --separation-policy exact_record
```

The CLI preparation step alone uses no database or model. Running a service still requires the documented compatible native environment. Use Docker rather than installing into the older WSL Python 3.10 runtime.

Native checks (no provider calls):

```bash
docker compose -p holdout-sentinel-s11 --env-file .env exec -T holdout \
  /app/.venv/bin/python -m pytest -q tests/test_stage11_native.py tests/test_checkpoint_native.py
```

The updated Dockerfile includes test and example files for this command. Restart the app without deleting the volume to separately inspect restored candidate decisions in the UI.
