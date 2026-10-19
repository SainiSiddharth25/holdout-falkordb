"""Stage 3: derived, evidence-bound graph memory (never model-authored verdicts).

These are *observations and procedure hints*, not independent evidence.
A memory is current only while its receipt, candidate endpoints, policy and
captured input binding match the active FalkorDB/evidence snapshot. Each note
is reconstructed from verified assessment receipts, not trusted LLM prose.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from .checkpoints import evidence_signature

KINDS = frozenset({'episodic', 'semantic', 'procedural'})


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def binding(candidate: dict, catalog_digest: str, policy: str) -> dict:
    evidence = candidate.get('evidence') or {}
    if not evidence.get('attempts'):
        raise ValueError('Memory requires a deterministic evidence assessment receipt.')
    return {'catalog_sha256': catalog_digest, 'separation_policy': policy,
            'candidate_id': candidate['id'], 'train_id': candidate['train_id'],
            'eval_id': candidate['eval_id'], 'verdict': candidate['status'],
            'evidence_sha256': evidence_signature(evidence),
            'reason_sha256': digest([{'method': a.get('method'), 'reason': a.get('reason')}
                                      for a in evidence['attempts']])}


def make_memories(candidate: dict, *, catalog_digest: str, policy: str,
                  revision: int, profiles: dict[str, dict] | None = None) -> list[dict]:
    """Build exactly three bounded memory records from an evidence receipt.

    No LLM text is accepted and an unresolved finding never becomes confirmed.
    The procedural record describes historical attempts, not a portable verdict.
    """
    bind = binding(candidate, catalog_digest, policy)
    attempts = candidate['evidence']['attempts']
    methods = [a['method'] for a in attempts]
    profiles = profiles or {}
    shape = {side: {'content_role': profiles.get(side, {}).get('content_role'),
                    'answer_symbols': bool(profiles.get(side, {}).get('answer_symbols'))}
             for side in ('train', 'eval')}
    common = {'candidate_id': candidate['id'], 'binding': bind,
              'revision_observed': revision, 'source': 'deterministic_evidence_receipt',
              'status': candidate['status']}
    contents = {
        'episodic': {'attempted_methods': methods,
                     'attempt_results': [a['verdict'] for a in attempts],
                     'last_reason': str(attempts[-1].get('reason', ''))[:500],
                     'note': 'The evidence tools performed these checks in this order.'},
        'semantic': {'finding': (f"Candidate {candidate['id']} is {candidate['status']} "
                                  f"under {policy}; this statement is scoped to its captured evidence."),
                     'train_id': candidate['train_id'], 'eval_id': candidate['eval_id'],
                     'evidence_method': attempts[-1]['method'],
                     'uncertainty_explicit': candidate['status'] == 'unresolved'},
        'procedural': {'method_sequence': methods, 'last_result': candidate['status'],
                       'source_shape': shape, 'policy': policy,
                       'note': 'Historical method behavior only. Re-run evidence tools before any new verdict.'},
    }
    output = []
    for kind, content in contents.items():
        # Stable across checkpoint replay; never includes ephemeral graph revision.
        mid = digest({'kind': kind, 'binding': bind})[:32]
        output.append({**common, 'id': 'mem_' + mid, 'kind': kind,
                       'content': content, 'content_sha256': digest(content)})
    return output


def evaluate(mem: dict, candidate: dict | None, *, catalog_digest: str, policy: str,
             expected: dict | None = None) -> dict:
    """Fail closed when the recorded evidence is no longer authoritative."""
    if mem.get('kind') not in KINDS or candidate is None:
        status = 'stale'
        reason = 'Unknown memory kind or candidate no longer exists.'
    else:
        try:
            current = binding(candidate, catalog_digest, policy)
            status = 'current' if (mem.get('binding') == current and
                    mem.get('content_sha256') == digest(mem.get('content')) and
                    (expected is None or all(mem.get(key) == expected.get(key)
                      for key in ('id', 'kind', 'content', 'candidate_id', 'source', 'status')))) else 'stale'
            reason = ('Receipt and source-policy binding verified.' if status == 'current' else
                      'Evidence, endpoints, policy or captured source snapshot changed.')
        except (ValueError, KeyError, TypeError):
            status, reason = 'stale', 'Current evidence assessment is unavailable.'
    return {**mem, 'validity': status, 'validity_reason': reason}


def procedure_matches(mem: dict, pending: dict, policy: str) -> bool:
    """A weak *ordering suggestion*, never a transfer of factual conclusions."""
    if mem.get('kind') != 'procedural' or mem.get('validity') != 'current':
        return False
    content = mem.get('content', {})
    if content.get('policy') != policy:
        return False
    available = set(pending.get('pending_methods', []))
    return bool(available.intersection(content.get('method_sequence', [])))
