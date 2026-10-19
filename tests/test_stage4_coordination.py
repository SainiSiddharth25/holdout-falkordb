"""Offline Stage 4 service tests with an explicit NetworkX test adapter."""
from pathlib import Path
import pytest
from stage11_support import service_modules
from holdout.checkpoints import CheckpointStore

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/decision-impact/dataset.json'


def test_exclusive_claim_and_fencing(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'run')
        try:
            task=s.claim_investigation('code','worker-a')['claim']
            assert task['candidate_id']=='a-x'
            assert s.claim_investigation('code','worker-b')['claim']['candidate_id']!='a-x'
            with pytest.raises(service.RevisionConflict):
                s.investigate('a-x','exact')
            with pytest.raises(service.RevisionConflict):
                s.investigate('a-x','exact',lease_token='f'*32)
            with pytest.raises(service.RevisionConflict):
                s.finish_investigation('a-x','f'*32)
            assert s.candidate('a-x')['status']=='unresolved'
            s.investigate('a-x','exact',lease_token=task['lease_token'])
            result=s.finish_investigation('a-x',task['lease_token'])
            assert result['state']=='done'
            with pytest.raises(service.RevisionConflict):
                s.finish_investigation('a-x',task['lease_token'])
            assert s.candidate('a-x')['status']=='confirmed'
        finally:s.close()


def test_code_to_provenance_handoff_never_transfers_a_verdict(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'run')
        try:
            # 'high': shared scaffold, code alone cannot prove answer reuse.
            while True:
                claim=s.claim_investigation('code','code-specialist')['claim']
                assert claim
                cid=claim['candidate_id']
                for m in ('record_identity','exact','normalized_ast'):
                    if m in s.review_candidate(s.candidate(cid))['pending_methods']:
                        s.investigate(cid,m,lease_token=claim['lease_token'])
                        if s.candidate(cid)['status']!='unresolved':break
                choice=s._task_decisions()[cid]
                target=choice['role'] if not choice['done'] else None
                s.finish_investigation(cid,claim['lease_token'],handoff_role=target)
                if cid=='high':break
            assert s.candidate('high')['status']=='unresolved'
            task=s.store.task('high')
            assert task['state']=='pending' and task['role']=='provenance' and task['handoffs']==1
            assert any(ev['tool']=='task_handoff' and ev['candidate_id']=='high' for ev in s.store.events())
            claim=s.claim_investigation('provenance','lineage-specialist')['claim']
            assert claim and claim['candidate_id']=='high'
            s.investigate('high','provenance',lease_token=claim['lease_token'])
            assert s.candidate('high')['status']=='rejected'
            s.finish_investigation('high',claim['lease_token'])
            assert s.store.task('high')['state']=='done'
        finally:s.close()


def test_unfinished_claim_is_not_trusted_after_recovery(tmp_path):
    with service_modules() as service:
        store=CheckpointStore(tmp_path/'checkpoints')
        s=service.HoldoutService(FIXTURE,tmp_path/'first',checkpoint_store=store)
        old=s.claim_investigation('code','worker')['claim']
        s.investigate('a-x','exact',lease_token=old['lease_token'])
        rid=s.run_id
        s.close()
        recovered=service.HoldoutService(FIXTURE,tmp_path/'second',checkpoint_store=store,resume_id=rid)
        try:
            assert recovered.candidate('a-x')['status']=='confirmed'
            assert recovered.store.task('a-x')['state']=='done'
            assert not any(t['state']=='leased' for t in recovered.coordination_status()['tasks'])
            with pytest.raises(service.RevisionConflict):
                recovered.finish_investigation('a-x',old['lease_token'])
            assert recovered.agent['status']=='interrupted'
        finally:recovered.close()


def test_policy_handoff_must_be_relevant_and_task_not_dropped(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'run')
        try:
            claim=s.claim_investigation('code','coder')['claim']
            with pytest.raises(ValueError):
                s.finish_investigation(claim['candidate_id'],claim['lease_token'],handoff_role='provenance')
            with pytest.raises(ValueError):
                s.finish_investigation(claim['candidate_id'],claim['lease_token'])
            assert s.store.task(claim['candidate_id'])['state']=='leased'
        finally:s.close()


def test_token_is_not_exposed_in_public_status(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'run')
        try:
            claim=s.claim_investigation('code','worker')['claim']
            assert claim['lease_token'] not in str(s.state())
            assert claim['lease_token'] not in str(s.coordination_status())
        finally:s.close()
