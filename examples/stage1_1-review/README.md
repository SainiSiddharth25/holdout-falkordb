# Stage 1.1 policy comparison

These self-authored inputs contain five records per pool and produce five candidate pairs: identical multi-function code, an identical class, explicitly identified scaffold, a renamed declared answer, and an unsupported shared-source reference.

Import the two JSONL files using Equal preservation weights. Run them once under `answer_leakage` and once as a new import under `exact_record`.

Expected evidence outcomes:

- Answer-leakage: 1 confirmed, 0 rejected, 4 unresolved; conservative cost 5.
- Exact-record: 3 confirmed, 2 rejected under the narrow exact-only rule, 0 unresolved; cost 3.

The different costs reflect **different guarantees**, not better detection accuracy. See `docs/STAGE1_1_EVIDENCE_REVIEW.md`. These expectations are not included in the imported rows or exposed to the agent as labels.
