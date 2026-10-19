# Stage 2 architecture addendum

Stage 2 adds `planning.py` and `optimizer.solve_counterfactual`. Hypothetical inputs never mutate Candidate verdicts or the authoritative release cache. All nontrivial scenario solves use the same native max-flow path. A revision-bound plan cache stores full certificates separately from evidence; HTTP/MCP only expose bounded summaries until a report is requested. Page polling never recomputes counterfactuals. `graph_guided` is an additional deterministic policy; the original endpoint-heuristic policy remains for comparison. The LLM has an eighth tool, `plan_investigation`, and bounded summaries rather than full scenario networks in its context. See [Stage 2](STAGE2_ACTIVE_INVESTIGATION.md).

The description below records earlier-stage architecture and baseline behavior. Where it mentions seven tools or the endpoint heuristic, that is the retained baseline, not the new planner.

# Stage 1.1 architecture addendum

This file's original algorithm/provenance description below is retained as the baseline. New imports also carry an immutable `separation_policy`, stored on `ReleaseMeta` and bound into schema-2 release receipts. `record_identity` checks complete captured bytes; it confirms/rejects exact-only candidates but remains an observation under answer-leakage policy. New `review.py` exposes method applicability and split retention without introducing a replacement solver. `verify_release` recomputes the release review. Historical schema-1 receipts remain readable. See [Stage 1.1 contracts](STAGE1_1_EVIDENCE_REVIEW.md) for scope and compatibility.

---

# Architecture and correctness

## Evidence-to-action loop

The review client reads a revisioned `HoldoutService`. Agents connect through the actual MCP SDK protocol and select seven domain tools. The same service backs HTTP actions and external MCP clients.

`GraphStore` makes FalkorDB authoritative for records, candidate states, provenance and tool events. `Entity.data` preserves enriched provenance metadata; `Record.record_data` preserves exact original record metadata for release receipts. A candidate update increments the release revision and invalidates cached optimization results and any current export.

The service holds a reentrant lock around compound actions. Native optimization checks the revision before and after solving. Export accepts an optional expected revision and verifies current catalog/artifact hashes. The local UI prevents simultaneous manual mutations while an investigator is running. Each standalone process owns its run; an external server can host independently named runs.

## Bounded evidence rules

Exact comparison operates on declared independently solution-bearing functions, including functions contained inside a bundle. Shared helpers are not silently treated as answers.

The normalized AST comparator renames local bindings and the function name within a restricted lexical subset. Constants, operators, globals, attributes and other meaningful structure remain. Unsupported nested scopes and constructs stay unresolved. A mismatch never proves independence.

FalkorDB returns bounded multihop provenance paths. Assessment validates only those supplied paths against the fixture. Every confirming hop requires a captured transformation receipt whose endpoints and solution hashes match real bytes, and the solution payload must actually occur in the captured endpoints. Merely asserting a common source or setting a preservation flag cannot confirm leakage.

The scaffold rejection is scoped to a hypothesis raised solely because of a common scaffold. Paths must be present in the captured graph, and the declared answer functions must be inspected. This rejects that stated basis without claiming a complete semantic independence proof.

Record text is read as data and never executed. Paths are bounded to the fixture root; escaped/symlink paths and excessive artifacts are rejected. Model tools accept record/candidate IDs, allowlisted methods and bounded depth, with no raw Cypher, shell, URL, verdict or output-path parameter.

## Exact optimization scope

The objective is `min Σ w(v) x(v)` with binary exclusion variables and constraints `x(t)+x(e) >= 1` for every active pair. Required records have `x(v)=0`. This is minimum weighted vertex cover on a bipartite graph with fixed roles.

The flow construction uses source-to-train cost edges, train-to-eval conflict edges with `M = sum(cost)+1`, and eval-to-sink cost edges. Required-record cost edges receive capacity `M`. A conflict joining two required records is immediately infeasible; otherwise excluding every optional incident record supplies a cut cheaper than `M`.

For the source-reachable residual set `R`, the repair excludes training vertices outside `R` and evaluation vertices inside `R`. A train/eval conflict cannot run from a retained training vertex to a retained evaluation vertex without crossing an `M` edge, so the finite cut covers every conflict. Every valid cover likewise defines a finite cut of equal weight. Native max-flow/min-cut solves the desired problem exactly.

Costs are nonnegative integers and the sum is bounded below `2**52 - 1`. This keeps capacities exactly representable in the engine's numeric values. Duplicate candidate claims for the same pair contribute one capacity constraint. Zero-cost records and disconnected components are covered by tests.

## Why the returned flow is not the cut

`algo.maxFlow` returns positive-flow edges. These are not the records to exclude and are insufficient by themselves to reconstruct the residual graph. HOLDOUT reads every original capacity edge, attaches zero flow to omitted edges, and adds forward residual edges for unused capacity and reverse residual edges for positive flow.

The certificate requires valid integral capacities/flows, conservation, correct terminal flow, no residual source-to-sink augmenting path, and equality of flow value, cut capacity and exclusion cost. It also explicitly verifies every active conflict and required record. A standalone verifier reconstructs the expected capacity network from the declared input policy before accepting the supplied flow, so a forged smaller network cannot certify the original problem.

The empty-record case is handled by a marked zero-flow/zero-cut proof; the native procedure is not falsely reported as invoked on that case.

## Uncertainty and investigation priority

Confirmed-only constraints yield the lower optimum. Confirmed and unresolved constraints yield the conservative upper optimum, if feasible. Export always uses the latter. A rejected candidate is removed from this projection, while its provenance and rejection receipt remain recorded.

The deterministic priority uses endpoint cost and whether the current lower-bound exclusion set already covers a candidate. It is labeled a heuristic; it is not an exact value-of-information computation. Each automatic investigation is attempted once per run. Unknowns with exhausted evidence remain visible and conservatively covered.

## Export verification

The three exported files are revision-bound:

- `train.jsonl` and `eval.jsonl` contain exactly the retained source records, including full UTF-8 content, original hashes and metadata.
- `repair.json` contains the record policy, candidate statuses and inspection receipts, original artifact/catalog hashes, exact solution and flow/cut certificate, and hashes of the two manifests.

The in-service verifier checks the complete disjoint partition, exclusions/cost, protected records, active-conflict coverage, exact retained IDs, hashes and preserved content. It checks the certificate against reconstructed capacities. With original fixture evidence available, it additionally checks catalog identity and replays recorded evidence methods, including graph path receipts, against the retained original captures.

Offline verification without the original fixture is explicitly labeled mathematical/content consistency. It cannot establish that a replaced receipt is authentic. Hashes are not signatures, and a fully rewritten bundle plus rewritten source evidence is outside this consistency model.

## Execution modes

The deterministic policy is reproducible and independent of credentials. The optional live policy uses an operator-configured Responses or Chat Completions tool endpoint and genuine MCP sessions. Responses mode uses stateless requests, retains every returned output item for continuity, and matches function results to their call IDs. It validates a completed response before any tool action and requires a verified release plus final text to finish. Local reservations and call limits bound continued spending; the elapsed-time limit stops new work, while an in-flight operation can finish. Actual live-provider behavior is not claimed to have been tested. Setup and primary API references are in `live-agent-setup.md`.

The hosted static interface contains captured real states and exports. It explicitly says **Recorded run**. A local backend connection enables real investigation and release actions. The static deployment has no FalkorDB connection or model credentials.

## v2 import and source-reference graph

The new import step precedes the unchanged evidence investigation. `holdout.ingest` accepts two local JSONL/JSON/CSV inputs and writes a new snapshot (`dataset.json` plus content files). User IDs are retained as metadata while internal IDs are unique across the two pools. Candidate discovery uses indexed exact content/function fingerprints, bounded normalized Python AST, five-token shingle similarity, and shared imported source references. All discovered candidates begin `unresolved`.

If input metadata includes `source_id`, `parent_id`, `derived_from`, or `parent_ids`, the importer creates `Entity` nodes with `kind:unverified_source_reference` and `DERIVED_FROM` relationships. **These are hints about possible ancestry, not trusted proof of answer preservation.** Unlike the curated demo, imported metadata alone has no captured, hash-bound edge manifests; the original `assess_lineage` checks therefore cannot confirm such paths without independently acquired captures. The application never fabricates missing sources or follows external URLs.

Per-record costs are assigned from one approved policy (`equal`, `balanced`, `eval_first`, `train_first`) before graph seeding. `protected` remains an input flag. After seeding, evidence checks, FalkorDB's native `algo.maxFlow` reduction, and receipt verification are unchanged. The optimized release remains scoped to **the discovered candidate graph**, which is not exhaustive.
