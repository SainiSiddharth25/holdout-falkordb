# Development status after Stage 2 implementation (2026-10-19)

Stage 2 source now contains bounded native counterfactual ranking, separate graph-guided and exhaustive runners, safe finite-bound stopping, a report verifier and UI. Offline and browser contract tests are recorded in STAGE2_VALIDATION.md; native and live-provider promotion remain separate. No general real-corpus or LLM cost advantage is claimed. The roadmap below is preserved as the original staged plan, not a claim that all original exit gates are already met.

# HOLDOUT Sentinel — one project, three complementary capabilities

**Development proposal, updated 2026-10-19.** The submitted HOLDOUT implementation is the baseline. Work described below is **not** an assertion that all three tracks have been implemented or that a past submission can be amended. Ship Sentinel separately and retain the submitted build/tag.

## Product promise

> An integrity control plane for AI dataset releases: discover suspected answer overlap, investigate evidence, retain valid findings across sessions, apply organizational policies and approvals, and issue evidence-backed, checkable release artifacts.

FalkorDB is the central connected state store and native solver; exact evidence judgments and release verification stay deterministic. An LLM or specialized agents may choose investigations but may not invent a verdict, change a retention policy, or override a verifier.

## One workflow, three requirements

- **Track 1 — Agents act on connected data:** candidate discovery → source/provenance evidence → native max-flow repair → verified manifests. Changing a grounded graph edge can change the optimal repair.
- **Track 2 — Agent memory and coordination:** evidence-version-bound investigation episodes and procedures; independent workers acquire tasks with revision/lease checks and resume without repeating verified work. Memory must become invalid when evidence changes. Cross-tenant access is denied.
- **Track 3 — Company brain:** dataset/project/benchmark ownership, policies, decisions and reviewers are connected to evidence; approved organizational constraints alter release eligibility; reviewer routing is grounded in authoritative organizational records.

The same `ReleaseVersion` is the join point. Do not build three different copies of HOLDOUT or let an ungrounded company-memory answer become evidence of code contamination.

## Stages and exit gates

| Stage | Scope | Non-negotiable pass gate | Status |
|---|---|---|---|
| 0 — Durable single-agent foundation | Restore exact candidate judgments from versioned, fingerprinted fixture snapshots; safe source-change refusal; bounded context; explicit failure messages | Restart local service after investigating, verify same results without contacting a model; tampered source must fail closed; restart a verified release and recheck its certificate | **Initial implementation in this preview; native restart test pending** |
| 1 — Real-source discovery | Local repo/history ingestion, pinned Git revisions, dataset manifests, provenance claims, retrieval coverage report | Fixture with concealed known overlaps: report candidate precision/recall, false positives, evidence origin; no fabricated lineage | Planned |
| 2 — Active graph investigation | Per-edge counterfactual cut sensitivity, investigative cost/uncertainty model, evidence-coverage stopping conditions | Beats an equal-budget deterministic baseline on useful questions answered or measured investigation cost; correct optimality bounds | Planned |
| 3 — Agent memory | Ephemeral vs durable episodic/semantic/procedural records, `SUPPORTED_BY`, supersession, retrieval and invalidation | Interrupted sessions safely reuse evidence, stale memory is automatically invalidated and never treated as confirmed evidence | Planned |
| 4 — Coordination | Multiple specialized MCP investigators with task leases, atomic claim/complete, idempotence and conflict arbitration | Crash/retry and concurrent-agent tests: no duplicate released decisions, no revision loss, correct handoffs, tenant boundary tests | Planned |
| 5 — Organization context | `Project`, `DatasetOwner`, `Benchmark`, `PolicyVersion`, `Approval`, `Decision`, `Evidence` | Change an authoritative policy/owner and show a different permission or approval route, with grounded source trace | Planned |
| 6 — Release Sentinel | Versioned input change detection, target invalidation, approval workflow, release gate and notifications | A changed dataset, source capture, or policy triggers only necessary reassessment; invalid or unapproved release never exported as approved | Planned |
| 7 — Production research | Larger real, licensed/public corpora; realistic baselines; permissioned service hardening | Full end-to-end quality, correctness, token cost, speed, memory footprint, security and operator usability measurements | Planned |

### Ordering and scope decisions

1. Stage 0 first: no memory/orchestration layer on top of restart fragility.
2. Stages 1 and 2 before elaborate multi-agent work: if candidate discovery is poor, parallel agents merely accelerate mistakes.
3. Stages 3–4 add actual cross-session state, not just a chat log or multiple concurrent LLM requests.
4. Stage 5 makes policy and ownership affect release decisions, not just appear in a decorative search graph.
5. Stage 6 adds incremental invalidation and operational workflows; exact minimum-cut is recomputed as required, **not claimed to be incremental**.

## Architecture invariants

- Actual content and exact input versions are immutable per release attempt.
- Candidate retrieval is a *hypothesis*, never proof; a source ID alone cannot verify answer lineage.
- Protected records never disappear because of agent choice.
- Costs originate from a reviewed, versioned policy before optimization, not from the desired answer.
- The exact bipartite minimum-cut proof applies only to pairwise train/eval conflicts with additive exclusion costs. Coverage quotas, replacement planning and arbitrary split assignments require different solvers and certificates.
- Native FalkorDB algorithm output plus independent verifier are mandatory for a verified repair.
- The confirmed-only optimum is a lower bound; the conservative optimum covers confirmed and unresolved edges.
- Memory is cached knowledge with versioned support, never an independent truth authority.
- Organizational and tenant access policies must be enforced on every API/tool path, including the memory layer.

## Fair comparison plan

Use identical captured evidence, prompts/model budget and action budget. Baselines include (a) hash/AST/LSH retrieval with a fixed inspection policy, (b) equivalent indexed relational schema + strong in-memory optimization, and (c) same graph with deterministic rather than LLM investigative choice. Report detection precision/recall, missed answer leaks, verified retained utility cost, release correctness, model token/latency cost, total time, rerun cost, and restart recovery. No claim of FalkorDB speed advantage is justified by the existing small benchmark.

## One eventual all-three-track demo

New code shard appears → retrieval proposes candidate leaks → Track 1 agents investigate and optimize → one worker crashes → Track 2 memory/lease permits a second agent to resume valid evidence → graph-linked project policy identifies who can approve the change (Track 3) → verified repair and organizational approval gate issue a versioned release; modifying a source or policy correctly invalidates dependent conclusions.

## Stage 1.1 checkpoint

The user reported a complete Stage 1 Git-import release (70 records, 43 candidates, cost 35). Stage 1.1 adds policy-scoped whole-record identity, method applicability, per-split review and empty-pool warnings. Offline regression and browser checks are recorded in STAGE1_1_VALIDATION.md; native acceptance remains separate. A controlled counterfactual fixture is present, but Stage 2 prioritization is not implemented. No detector accuracy or native performance advantage is inferred from the copied-snapshot smoke test.
