"""Offline Stage 3 evidence-bound memory behavior. No native engine or MCP."""
from copy import deepcopy
from pathlib import Path
import pytest

from holdout.memory import make_memories, evaluate
from holdout.checkpoints import CheckpointStore
from stage11_support import service_modules

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'fixtures/decision-impact/dataset.json'


def test_memory_builder_rejects_unbacked_finding():
    with pytest.raises(ValueError):
        make_memories({'id':'c','train_id':'t','eval_id':'e','status':'confirmed'},
                      catalog_digest='a'*64, policy='answer_leakage', revision=2)


def test_memory_types_binding_and_payload_tamper():
    c={'id':'c','train_id':'t','eval_id':'e','status':'confirmed',
       'evidence':{'attempts':[{'method':'exact','verdict':'confirmed','reason':'match','evidence':['bounded']} ]}}
    items=make_memories(c,catalog_digest='a'*64,policy='answer_leakage',revision=2)
    assert {m['kind'] for m in items} == {'episodic','semantic','procedural'}
    assert len({m['id'] for m in items}) == 3
    for m in items:
        assert evaluate(m,c,catalog_digest='a'*64,policy='answer_leakage',expected=m)['validity']=='current'
        assert evaluate(m,c,catalog_digest='a'*64,policy='exact_record',expected=m)['validity']=='stale'
        tampered=deepcopy(m);tampered['content']['note']='FAKE' if 'note' in tampered['content'] else 'FORGED'
        assert evaluate(tampered,c,catalog_digest='a'*64,policy='answer_leakage',expected=m)['validity']=='stale'
    modified=deepcopy(c); modified['evidence']['attempts'][0]['verdict']='rejected'
    assert all(evaluate(m,modified,catalog_digest='a'*64,policy='answer_leakage')['validity']=='stale' for m in items)


def test_recall_and_procedures_are_derived_not_authoritative(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'run')
        try:
            assert s.memory_overview()['current_total']==0
            result=s.investigate('a-x','exact')
            assert result['status']=='confirmed'
            mem=s.recall_memories('a-x')
            assert mem['count']==3
            assert {m['kind'] for m in mem['items']}=={'episodic','semantic','procedural'}
            assert s.state()['memory']['current_total']==3
            assert s.memory_overview()['counts']['stale']==0
            assert s.suggest_procedures('a-y')['candidate_id']=='a-y'
            assert s.candidate('a-y')['status']=='unresolved'
            assert s.store.revision()==2
            with pytest.raises(KeyError):s.recall_memories('non-existent')
            # Arbitrary graph-memory content tampering must not become trusted.
            target=mem['items'][0]['id'];s.store._memories[target]['content']['note']='new model assertion'
            s._memory_cache=None
            assert s.memory_overview()['counts']['stale']==1
            assert s.recall_memories('a-x')['count']==2
            assert s.recall_memories('a-x',include_stale=True)['count']==3
            # A receipt-changing revision invalidates ALL three old memories.
            original=s.candidate('a-x')
            altered=deepcopy(original['evidence'])
            altered['attempts'][0]['reason']='edited evidence'
            s.store.set_candidate('a-x','confirmed',altered)
            assert s.recall_memories('a-x')['count']==0
            assert s.recall_memories('a-x',include_stale=True)['stale_suppressed']==0
        finally:s.close()


def test_replay_reconstructs_verified_memories_not_model_text(tmp_path):
    with service_modules() as service:
        cp=CheckpointStore(tmp_path/'checkpoints')
        s=service.HoldoutService(FIXTURE,tmp_path/'one',checkpoint_store=cp)
        s.investigate('a-x','exact')
        s.investigate('high','provenance')
        original={m['id']:m['binding'] for m in s.recall_memories()['items']}
        assert len(original)==6
        rid=s.run_id;s.close()
        s=service.HoldoutService(FIXTURE,tmp_path/'two',checkpoint_store=cp,resume_id=rid)
        try:
            replayed={m['id']:m['binding'] for m in s.recall_memories()['items']}
            assert replayed==original
            assert s.agent['status']=='interrupted'
            assert s.store.revision()==3
            assert any(e['tool']=='session_resumed' for e in s.store.events())
            assert all(m['source']=='deterministic_evidence_receipt' for m in s.recall_memories()['items'])
        finally:s.close()


def test_unrelated_revision_does_not_invalidate_memory(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'run')
        try:
            s.investigate('a-x','exact')
            before=[m['id'] for m in s.recall_memories('a-x')['items']]
            s.investigate('a-y','exact')
            assert [m['id'] for m in s.recall_memories('a-x')['items']]==before
            assert s.memory_overview()['current_total']==6
        finally:s.close()
