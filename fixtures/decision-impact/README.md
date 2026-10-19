# Controlled sensitivity fixture for Stage 2

Self-authored test data, not a public-corpus benchmark or an implemented planning algorithm.

`a-x`, `a-y`, and `b-y` have supported exact answer matches. After they are confirmed, leave `high` and `redundant` unresolved. Costs were declared before running the test. `tC` is protected.

Exhaustive subset enumeration gives:

- Confirmed-only optimum: 3.
- Conservative optimum with both unknowns: 11.
- Rejecting `high`: conservative optimum 3.
- Rejecting only `redundant`: conservative optimum stays 11.

`high` is specifically a shared-scaffold hypothesis. The captured scaffold and supported differing answer functions allow that narrow claim to be rejected by the existing provenance assessment. `redundant` lacks a source capture and may remain unresolved; its counterfactual rejection is a mathematical test, not an evidence verdict.

Actual paths for an investigation must still come from FalkorDB. Test expectations are kept out of `dataset.json`. The agent sees the source evidence and hypotheses, not these intended outcomes.
