# Track 1 problem statement: HOLDOUT

## Hook

An AI team's evaluation looks strong. Then an engineer notices that several evaluation solutions may have reappeared in the draft training release through renamed functions, copied notebooks and transformed source captures. Removing everything suspicious wastes useful data. Clearing uncertain pairs by guesswork risks releasing a contaminated evaluation.

**Can an agent investigate the evidence, transform the resulting constraints into a graph problem, and produce the cheapest defensible release repair?**

## Competitive-programming formulation

You are given a fixed training set `T`, a fixed evaluation set `E`, nonnegative integer exclusion cost `w(v)` for each record, a set of required records, and candidate incompatibilities between `T` and `E`.

Each candidate starts with a captured status: confirmed, rejected, or unresolved. Investigation tools may update the status based on bounded code comparisons and verified provenance evidence.

Find an exclusion set `S` of minimum total cost such that:

1. Every confirmed conflict has an endpoint in `S`.
2. For a conservative release, every unresolved candidate also has an endpoint in `S`.
3. No required record belongs to `S`.

Return the excluded and retained record IDs, an optimality certificate, revised manifests, and the evidence supporting each investigated decision. If an active conflict joins two required records, report infeasibility with that conflict as a witness.

The confirmed-only optimum is a lower bound on the true repair cost within the candidate catalog. The confirmed-plus-unresolved optimum is a conservative upper bound when feasible. These bounds depend on the evidence assessments being sound and apply only to the captured catalog.

## Small sample

| Record | Pool | Exclusion cost |
|---|---|---:|
| A | Training | 2 |
| B | Training | 10 |
| X | Evaluation | 10 |
| Y | Evaluation | 1 |

Confirmed conflicts are `(A,X)`, `(A,Y)`, and `(B,Y)`. No records are required.

**Output:** exclude `A` and `Y`; cost `3`. All three conflicts are covered. Excluding all incident training records costs `12`; excluding all incident evaluation records costs `11`.

This example occurs within the larger fixture. It exposes why a blanket deletion policy is wasteful even before investigating which candidate edges are justified.

## Graph transformation

Construct source `s` and sink `z`:

- `s → t` for each training record, capacity `w(t)`.
- `t → e` for every active conflict, capacity `M`.
- `e → z` for each evaluation record, capacity `w(e)`.

Use `M = 1 + sum(w(v))`, and also use capacity `M` on the exclusion edge of a required record. For a finite minimum cut, exclude training records on the sink side and evaluation records on the source side.

No finite optimum crosses a conflict edge or excludes a required record. Therefore the finite cut is a valid weighted vertex cover. Conversely, every valid exclusion set yields a cut of the same cost. Minimum cut is therefore exactly the desired minimum exclusion cost.

## What the agent contributes

The combinatorial solver does not decide whether a suspicious relationship represents answer leakage. The investigator chooses tools, examines captured source and graph provenance, observes revised bounds, and decides whether more investigation is useful before staging a conservative release.

The current deterministic baseline investigates candidates in a declared heuristic order and runs exact, normalized-AST and provenance checks until evidence resolves a candidate or the methods are exhausted. The optional model policy chooses those tools through MCP. Both use the same evidence rules, optimizer and release verifier.

## Why this is timely

The motivation comes from current concern about evaluation contamination and the cost of data decontamination, including OpenAI's February 2026 explanation of changes to SWE-bench Verified reporting and the October 2026 research question of which RLVR data to remove. Those sources motivate the problem; this synthetic prototype does not reproduce or validate their experiments.

Primary context:

- https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
- https://arxiv.org/abs/2610.21229
- https://docs.falkordb.com/algorithms/maxflow
