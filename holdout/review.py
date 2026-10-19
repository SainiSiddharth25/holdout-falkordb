"""Evidence observations, method applicability, and inspectable release effects.

No traversal, verdict invention, or optimizer is hidden in the review layer.
Content observations are recomputed from captured bytes. Only record_identity
can adjudicate exact-record policy, and it is replayed like every other method.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .evidence import (_answer_units, _entity_bytes, _normalized, _record, _sha,
                       EvidenceError)
from .policies import policy_description, separation_policy


def identity_observation(fixture: dict, train_id: str, eval_id: str) -> dict:
    train, evaluation = _record(fixture, train_id), _record(fixture, eval_id)
    if train['split'] != 'train' or evaluation['split'] != 'eval':
        raise EvidenceError('Record identity requires a train/eval pair.')
    a, b = _entity_bytes(fixture, train_id), _entity_bytes(fixture, eval_id)
    if a is None or b is None:
        raise EvidenceError('Record identity requires both captured artifacts.')
    # Byte equality is checked in addition to hashes; no imported hash is trusted.
    return {'kind': 'whole_record_identity', 'identical': a == b,
            'train_id': train_id, 'eval_id': eval_id,
            'train_sha256': _sha(a), 'eval_sha256': _sha(b),
            'train_bytes': len(a), 'eval_bytes': len(b),
            'scope': 'Complete captured content bytes; record IDs and metadata are not compared.'}


def compare_record_identity(fixture: dict, train_id: str, eval_id: str) -> dict:
    observation = identity_observation(fixture, train_id, eval_id)
    policy = separation_policy(fixture)
    if policy == 'exact_record':
        verdict = 'confirmed' if observation['identical'] else 'rejected'
        reason = ('The complete captured contents are byte-identical; the exact-record policy forbids retaining both.'
                  if observation['identical'] else
                  'The complete captured contents differ. This pair is outside the exact-record-only conflict rule; answer independence has not been established.')
    else:
        verdict = 'unresolved'
        reason = ('Identical complete contents were verified, but identity alone does not establish answer relevance under the answer-leakage policy.'
                  if observation['identical'] else
                  'Complete contents differ; this does not establish answer independence. Answer comparisons or verified provenance may still establish a conflict.')
    return {'verdict': verdict, 'method': 'record_identity', 'reason': reason,
            'evidence': [observation, {'kind': 'separation_policy', 'id': policy}]}


def record_profile(fixture: dict, record_id: str) -> dict:
    record = _record(fixture, record_id)
    data = _entity_bytes(fixture, record_id)
    if data is None:
        return {'answer_symbols': [], 'normalized_symbols': [],
                'reason': 'No captured record content.', 'inferred_symbols': False}
    try:
        units, error = _answer_units(record, data.decode('utf-8'))
    except UnicodeDecodeError:
        units, error = [], 'Answer comparison requires UTF-8 Python content.'
    return {'answer_symbols': [name for name, _, _ in units],
            'normalized_symbols': [name for name, node, _ in units if _normalized(node) is not None],
            'reason': error, 'inferred_symbols': bool(record.get('solution_symbols_inferred'))}


def candidate_review(fixture: dict, candidate: dict, attempted: set[str] | None = None) -> dict:
    """Method availability is advisory and never changes a conflict verdict.

    Looking for an outgoing catalog link is an applicability test, not ancestry
    discovery. Actual provenance paths still come only from FalkorDB.
    """
    tid, eid = candidate['train_id'], candidate['eval_id']
    profiles = {'train': record_profile(fixture, tid), 'eval': record_profile(fixture, eid)}
    policy = separation_policy(fixture)
    observation = identity_observation(fixture, tid, eid)
    methods = {'record_identity': {'available': True, 'reason': 'Compare complete captured contents and apply the selected separation policy.'}}
    for method, field in (('exact', 'answer_symbols'), ('normalized_ast', 'normalized_symbols')):
        available = policy == 'answer_leakage' and all(p[field] for p in profiles.values())
        if policy != 'answer_leakage':
            reason = 'Not part of the exact-record-only policy. Use record_identity.'
        elif not all(p['answer_symbols'] for p in profiles.values()):
            details = [side + ': ' + (p['reason'] or 'No supported answer functions.') for side, p in profiles.items() if not p['answer_symbols']]
            reason = ' '.join(details)
        elif not available:
            reason = 'At least one record has no answer function in the supported normalized-AST subset.'
        else:
            reason = 'Declared answer functions are available for this bounded comparison.'
            if any(p['inferred_symbols'] for p in profiles.values()):
                reason += ' Some answer symbols were inferred by the importer; review that assumption.'
        methods[method] = {'available': available, 'reason': reason}
    outgoing = {link['source'] for link in fixture.get('links', [])}
    lineage_available = policy == 'answer_leakage' and tid in outgoing and eid in outgoing
    methods['provenance'] = {
        'available': lineage_available,
        'reason': ('Graph traversal is available. Captured Git identity or metadata alone is not proof of solution preservation.' if lineage_available else
                   'Not part of the exact-record-only policy.' if policy != 'answer_leakage' else
                   'At least one record has no captured outgoing provenance links; the current graph cannot supply two lineage paths.')}
    attempted = attempted or set()
    for method, info in methods.items():
        info['attempted'] = method in attempted
        info['state'] = 'unavailable' if not info['available'] else 'exhausted' if info['attempted'] else 'available'
    status = candidate.get('status', 'unresolved')
    pending = [name for name, info in methods.items() if info['available'] and not info['attempted']] if status == 'unresolved' else []
    if not all(p['answer_symbols'] for p in profiles.values()):
        relevance = 'Not established: one or both records lack supported declared answer functions.'
    elif any(p['inferred_symbols'] for p in profiles.values()):
        relevance = 'Answer functions were partly inferred by the importer; this is a reviewable assumption, not independently verified relevance.'
    else:
        relevance = 'Answer functions were explicitly designated by the input; correctness of that designation remains an input assumption.'
    if status == 'confirmed':
        implication = 'At least one endpoint must be excluded under the selected policy.'
    elif status == 'rejected':
        implication = 'This candidate adds no conflict constraint under the selected policy; no global independence is certified.'
    else:
        implication = 'Conservative export must exclude at least one endpoint while this candidate is unresolved.'
    return {'policy': policy_description(fixture), 'identity': observation,
            'answer_relevance': relevance, 'record_profiles': profiles,
            'methods': methods, 'pending_methods': pending,
            'policy_verdict': status, 'release_implication': implication}


def release_review(records: list[dict], candidates: list[dict], solution: dict,
                   policy: str) -> dict[str, Any]:
    """Deterministic receipt summary; verified by recomputation, including ties."""
    excluded = set(solution['excluded'])
    retained = set(solution['kept'])
    by_split = {}
    warnings = []
    for split in ('train', 'eval'):
        pool = [r for r in records if r['split'] == split]
        by_split[split] = {'input': len(pool),
                           'retained': sum(r['id'] in retained for r in pool),
                           'excluded': sum(r['id'] in excluded for r in pool),
                           'exclusion_cost': sum(r['cost'] for r in pool if r['id'] in excluded),
                           'protected_retained': sum(r['id'] in retained and bool(r.get('protected')) for r in pool)}
        if pool and not by_split[split]['retained']:
            warnings.append({'code': 'empty_' + split + '_pool', 'split': split,
                             'message': f'The repaired {split} pool is empty. The minimum-cost objective permits this; no minimum size or balance is enforced.'})
    counts = {s: sum(c['status'] == s for c in candidates) for s in ('confirmed', 'rejected', 'unresolved')}
    covered = {s: sum(c['status'] == s and bool({c['train_id'], c['eval_id']} & excluded) for c in candidates)
               for s in ('confirmed', 'unresolved')}
    unknowns = []
    for c in sorted(candidates, key=lambda item: item['id']):
        if c['status'] != 'unresolved':
            continue
        evidence = c.get('evidence') or {}
        reason = evidence.get('reason') or 'No evidence method has adjudicated this candidate.'
        unknowns.append({'candidate_id': c['id'], 'train_id': c['train_id'], 'eval_id': c['eval_id'],
                         'reason': reason, 'covered_by': sorted({c['train_id'], c['eval_id']} & excluded)})
    return {'separation_policy': policy, 'by_split': by_split,
            'conflicts': {**counts, 'confirmed_covered': covered['confirmed'], 'unresolved_covered': covered['unresolved']},
            'unresolved': unknowns, 'unresolved_reason_counts': dict(sorted(Counter(u['reason'] for u in unknowns).items())),
            'warnings': warnings,
            'balance_guarantee': False,
            'note': 'Exclusion cost is optimal for the selected constraints. Split balance, minimum dataset size and downstream model quality are not guaranteed.'}
