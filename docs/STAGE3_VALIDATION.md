# Stage 3 validation report

2026-10-19. Stage 3 extends the archived Stage 2 source without altering submitted code.

## Verified in the authoring environment

- Pure Python memory generation and assessment: missing evidence rejected; all three kinds built from deterministic receipts; stored-content tampering, changed reason, modified evidence, and policy mismatch mark memories stale.
- Offline service contract tests: `recall_memories`, memory overviews, procedure suggestions, staleness after receipt changes, and no transfer of a verdict to a different claim.
- Checkpoint replay: memory identity preserved after deterministic receipt revalidation; unrelated candidate changes do not invalidate other candidates' memory.
- Previous Stage 1.1/Stage 2 service and planner offline regression tests remain available.
- JavaScript syntax, Python compilation, and source package checks.

**This environment has no `falkordb`, `falkordblite`, or `mcp` package installed**, so production database and MCP paths have **not** been executed during authoring. Do not describe offline NetworkX test adapters as native FalkorDB results.

## Pending native promotion checks

`tests/test_stage3_native.py` must run inside the provided Docker image and verify:

1. Real Cypher memory nodes, candidate edges and evidence-receipt relationships.
2. SDK MCP tool enumeration, memory retrieval and procedural hints.
3. Read-only memory tool calls do not mutate candidate decisions or the optimizer's evidence revision.
4. Restart checkpoint revalidates receipts and reconstructs stable memory IDs.
5. Tampering with memory text makes that note stale and does not rewrite the candidate verdict.

Stage 2 native solver, planning and checkpoint tests also must pass. Real Requesty model quality, memory retrieval usefulness, multiagent coordination, cross-release memory and throughput are **not** validated by these tests.
