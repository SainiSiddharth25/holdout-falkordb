# HOLDOUT Sentinel Stage 2 — graph-guided active investigation

Version 0.5.0, 2026-10-19. This extends Stage 1.1; it does not replace the submitted Track 1 build. Native promotion and live-provider validation status are in `STAGE2_VALIDATION.md`.

## The new decision

Previously the deterministic runner chose unresolved claims using an endpoint-cost heuristic and exhausted the applicable methods. Stage 2 adds a planner that asks: **which uncertain relationship can affect the least-cost repair?** The separate `graph_guided` runner consults that planner through MCP and can stop cost-focused investigation when the finite bounds agree. The live model receives the same tool and instructions to use it; the model remains free to choose tools and still must stage a real verified conservative release.

Every scenario is computed from the current FalkorDB snapshot using the existing capacity-network compilation and native `algo.maxFlow`, followed by independent certificate checks. Python computes rankings and compares certificates. There is no production NetworkX solver fallback. Empty networks and protected-to-protected infeasibility have explicit direct certificates rather than invented native calls.

Official native procedure contract: https://docs.falkordb.com/algorithms/maxflow . It returns positive-flow edges and values. The optimizer retains every original capacity edge when reconstructing the residual cut, including zero-flow edges.

## Mathematical contract

Let C be confirmed edges, Q unresolved claims, and f(S) the minimum additive exclusion cost covering the edges in S while preserving protected records. Rejected claims impose no constraint.

- Lower bound L = f(C).
- Conservative upper bound U = f(C ∪ Q).
- For one unresolved claim q, rejection branch U−q = f(C ∪ (Q \ {q})).
- Confirmation branch L+q = f(C ∪ {q}).
- Possible rejection saving = U − U−q, when both costs are finite.
- Confirmation uplift = L+q − L, when both costs are finite.

The scenarios assume an outcome only for one claim. The investigator must still obtain actual evidence to make that outcome real. Duplicate claims are separate hypotheses; one rejection does not remove another active claim on the same endpoint pair.

For finite feasible L = U, the current conservative repair is feasible for every intermediate graph C ⊆ S ⊆ C ∪ Q and its cost equals the lower bound. Therefore that same repair is optimal for every possible resolution of the captured unknown claims. This is a statement about optimal **cost**, not unique record selection, complete evidence resolution, unseen contamination, or useful split sizes.

Two infeasible bounds are NOT equality of finite costs. Confirmed infeasibility stops with an explicit obstruction and prevents export. Conservative-only infeasibility can make investigation of a protected-endpoint claim urgent.

## Ranking, work budgets and scope

Feasibility-restoring or infeasibility-establishing branches rank first. Otherwise the score is:

`max(possible_rejection_saving, confirmation_uplift) / pending_auto_method_effort`

Declared proxy units: whole-record identity 1, exact 1, normalized AST 2, provenance 3. Auto may finish before using all pending methods. These are **uncalibrated method-effort units**, not execution time, model tokens, money, or outcome probabilities. Answer-policy identity is not a verdict-producing method; if it is the only remaining method, the claim is classified as needing new evidence rather than repeatedly investigated.

Default: at most 32 actionable claims (two scenarios each) and 30 seconds checked between scenario operations. The native call in progress is allowed to finish. A 16 MiB report-payload budget also bounds retained scenario certificates. Initial lower/upper solve time is outside the planner's reported counterfactual-analysis time; benchmark wall time includes relevant end-to-end continuation work.

When there are more actionable claims than the cap, an explicitly documented legacy endpoint heuristic preselects the scan. The UI discloses evaluated versus actionable and unscanned counts. The displayed top eight are only a view of the evaluated set; the downloadable report contains all evaluated scenarios.

**Partial scans are not globally best rankings. Zero single-edge sensitivity is not a stopping proof.** Interacting uncertainties may matter jointly. Regression tests include duplicate hypotheses for which every single-claim score is zero while the overall gap remains open. Exhausted evidence, time/report budgets, and investigation budgets have distinct stop explanations. None silently rejects unresolved claims.

## State safety

Plans bind the run ID, evidence revision, source catalog digest, separation policy and candidate snapshot digest. One scenario uses a scratch flow graph and is always labelled hypothetical. It cannot call `set_candidate`, modify costs or protection, replace the release cache, or create an export.

Real investigation invalidates the plan cache. Source/catalog changes fail closed. Stale expected revisions return a conflict. Ordinary `/api/state` polling returns cached summary only and does not compute new scenarios. Planner events persist in the active evidence graph; checkpoint restoration replays actual evidence, not planner recommendations. After restart, recompute planning from the recovered graph.

`planning-report.json` includes the inputs, baseline and scenario certificates, per-claim arithmetic, scope and work counts. `verify-plan` independently reconstructs the capacity networks and checks the certified costs and displayed deltas. With the original fixture, it also replays the recorded evidence. A self-contained report is not a digital signature. It proves neither the optimal investigation order nor data authenticity beyond its supplied inputs.

## User controls

**Run live agent** stays the first primary button. **Run deterministic policy** remains the existing exhaustive endpoint baseline. In the new Planner panel:

- **Calculate priorities:** explicit read-only counterfactual computation; no model key.
- **Run graph-guided:** deterministic planner/evidence loop through actual MCP; no model key.
- **Planning report + certificates:** local download for the current revision.
- **Load sensitivity example:** creates a fresh active snapshot from a fixed local fixture after confirmation; existing saved files remain. No Git clone, URL fetch or provider request is made.

Both modes use the same original record policies, deterministic evidence tools, native repair solver and release verifier. No planner outcome becomes a verdict. Conservative export continues to cover all confirmed and unresolved claims.

## Local setup

Extract this archive into a new folder; do not overwrite the submitted build, Stage 1.1 checkout or runtime files. From the extracted `holdout` directory:

```bash
cp .env.example .env
# Optional: add your existing rotated Requesty key locally.
# Keep the new HOLDOUT_HOST_PORT=8060 rather than copying an old port setting.
set -a
source .env
set +a
docker compose -p holdout-sentinel-s2 --env-file .env up -d --build
```

Open http://127.0.0.1:8060 . The dedicated Compose project and volume leave 8050 and other previous stages alone. Ubuntu 24.04, Python and the native runtime remain in Docker; no host Python 3.12 install is required for this route.

Additional `.env` settings:

```dotenv
HOLDOUT_PLAN_MAX_CANDIDATES=32
HOLDOUT_PLAN_MAX_SECONDS=30
HOLDOUT_GUIDED_MAX_INVESTIGATIONS=24
```

The guided limit counts candidate auto-investigation batches, NOT model requests or individual underlying checks. Model-specific budgets continue to apply to the live mode. Full scenario proofs are excluded from the model tool summary to avoid filling its context with capacity networks. SDK MCP now exposes eight domain tools, adding `plan_investigation` to the previous seven.

## First meaningful example

Use **Load sensitivity example**, then **Calculate priorities**. All five claims initially start unresolved; no fixture truth labels are injected. The planner should put `high` first. Its rejection branch changes conservative cost from 11 to 3, before any actual claim is rejected.

Click **Run graph-guided**. The source-backed shared-scaffold assessment rejects `high`; subsequent supported answer checks establish the finite lower bound. The final conservative cost is 3, with a verified release. Unknowns may remain—this is expected. The redundant source claim lacks capture evidence and is still covered by the repair.

For the exact 3-versus-11 table from the Stage 1.1 sensitivity README, first confirm `a-x`, `a-y`, and `b-y` via their existing `exact` methods. The native acceptance test does that through real evidence calls. Then the two unknowns have rejection savings 8 and 0. Rejection of `high` gives equal finite costs 3 and 3. Do not manually set the intended statuses.

Using an identical snapshot on both sides remains useful for ingestion but is a poor investigation-value benchmark: many duplicate constraints already force the same repair cost. The new synthetic scenario is designed to make decision effects visible.

## CLI and API

Existing `demo` modes are joined by `--mode graph_guided`. `python -m holdout demo --fixture fixtures/decision-impact --mode graph_guided --output .runtime/sessions/stage2-run.json` starts a NEW one-shot run; it is not a resume of the open browser session. It contacts no model.

`python -m holdout plan --fixture fixtures/decision-impact --output .runtime/sessions/plan-new.json` writes a full report for a NEW read-only scenario session. Existing output files are refused. Both commands run inside the configured Docker container or properly installed native environment.

`python -m holdout verify-plan path/to/planning-report.json --fixture path/to/original/dataset.json` checks a saved report without starting a database or contacting a provider (project Python dependencies must be installed).

Live session endpoints: `POST /api/plan` with an optional expected revision and bounded scan cap; `GET /api/plan/report`; `POST /api/run {"mode":"graph_guided"}`. Planning cannot set evidence outcomes. Same-origin action and idle-run checks apply. This is still a local, single-active-release service, not a tenant-authenticated production deployment.

## Baselines and benchmarks

`scripts/benchmark_stage2.py` compares graph-guided, endpoint-with-gap-stop, catalogue-with-gap-stop and exhaustive endpoint selection. All start with the same primed evidence, use the same real auto assessments, receive the same batch budget, and export conservatively. The first three have the same finite-bound equality stopping rule, separating ranking from early stopping.

On the original sensitivity case all three sensible selectors choose `high` first: **no ranking advantage is claimed there**. The added `decision-impact-distractor` fixture contains an expensive claim whose resolution changes which repair is selected but not its cost. With one remaining batch, the counterfactual selector resolves `high`, lowering 31 to 23; the endpoint and catalogue selectors spend that batch on `e-w` and retain cost 31. With two batches those baselines also reach 23.

The checked-in report is explicitly an OFFLINE CONTRACT measurement using a NetworkX test adapter, not native throughput or MCP/LLM validation. It records additional scenario solves and wall time; planning is not free. It is a targeted regression, not a representative real-data evaluation or measured model-credit saving.

To run the native comparison after Docker acceptance:

```bash
docker compose -p holdout-sentinel-s2 --env-file .env exec -T holdout \
  /app/.venv/bin/python scripts/benchmark_stage2.py \
  --output /app/.runtime/sessions/stage2-native-benchmark.json
```

This uses native FalkorDB and direct service calls (no MCP or LLM). Do not pass `--offline-contract` when collecting native results.

## Remaining work

Native Stage 2 promotion tests, a real Requesty planning-tool run, larger independent corpora, calibrated investigation costs, group-sensitive lookahead, efficient native reuse across scenarios, and cross-session multi-agent memory remain distinct validation or development tasks. There is no speed advantage claim. Stages 3–5 of the Sentinel roadmap still cover evidence-bound memory, agent coordination and organizational policy; they are not implemented by adding a planner.
