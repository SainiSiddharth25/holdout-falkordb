# Controlled high-cost distractor (Stage 2)

Self-authored evidence; not a public-corpus benchmark. Prime `a-x`, `a-y`, `b-y`, and `d-w` with real exact checks. Do not assign known verdicts directly.

The added component has two training records and one evaluation record, all with cost 20. The confirmed d-w edge has an optimum of 20. The extra unresolved e-w edge can change the chosen optimal exclusion but not its cost. An endpoint heuristic based on the particular confirmed-only cut scores this at 20 and places it ahead of the truly cost-changing claim high (endpoint estimate 10).

Expected full bounds after priming: 23 and 31. Counterfactual ranking prioritizes high: rejection changes 31 to 23. The expensive e-w branch has zero cost sensitivity. With the same bound-equality stopping rule and same auto investigations, the endpoint heuristic spends a batch on e-w before high; graph-guided planning spends one on high. Unknowns still must be covered by the exported conservative repair.

This is a deliberately targeted regression showing a weakness of an endpoint proxy, not evidence of average-case speedup or an LLM improvement. All strategies receive identical records, pending-method information, evidence and costs. Scenario-solve overhead must be measured separately.
