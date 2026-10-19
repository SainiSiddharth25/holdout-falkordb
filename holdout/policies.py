"""Versioned separation semantics, independent of retention-cost weights."""
from __future__ import annotations

DEFAULT_SEPARATION_POLICY = 'answer_leakage'
SEPARATION_POLICIES = {
    'answer_leakage': {
        'label': 'Answer-leakage separation',
        'rule': 'Require supported answer-function overlap or verified solution-preserving lineage. Whole-record identity alone does not establish answer relevance.',
        'scope': 'Bounded answer checks; inferred answer symbols remain a heuristic requiring review. A mismatch is not proof of independence.',
    },
    'exact_record': {
        'label': 'Exact-record separation',
        'rule': 'Forbid byte-identical complete captured record contents across train and eval. Unequal contents are outside this exact-only conflict rule.',
        'scope': 'Exact contents only, not renamed answers, partial overlaps, semantic equivalence, or undiscovered answer leakage.',
    },
}


def validate_separation_policy(value: str) -> str:
    if not isinstance(value, str) or value not in SEPARATION_POLICIES:
        raise ValueError('separation_policy must be answer_leakage or exact_record.')
    return value


def separation_policy(fixture: dict) -> str:
    policy = fixture.get('policy', {})
    if not isinstance(policy, dict):
        raise ValueError('Fixture policy must be an object.')
    return validate_separation_policy(policy.get('separation_policy', DEFAULT_SEPARATION_POLICY))


def policy_description(fixture: dict) -> dict:
    selected = separation_policy(fixture)
    return {'id': selected, **SEPARATION_POLICIES[selected]}
