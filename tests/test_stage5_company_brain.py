"""Stage 5 offline governance checks with explicitly simulated graph queries."""
from pathlib import Path
import json
import pytest
from stage11_support import service_modules

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/decision-impact/dataset.json'


def staged(s):
    for candidate in s.store.candidates():
        s.investigate(candidate['id'])
    result=s.stage_release()
    assert result['verified'] and result['cost']==3
    return result


def test_owner_policy_evidence_and_gate_changes_decision(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'one')
        try:
            ctx=s.organization_context()
            assert {p['dataset']['split'] for p in ctx['datasets_and_owners']}=={'train','eval'}
            assert {x['owner']['role'] for x in ctx['datasets_and_owners']}=={'evaluation_owner','training_owner'}
            assert ctx['gate']['status']=='not_staged'
            staged(s)
            gate=s.organization_context()['gate']
            assert gate['retained']=={'train':2,'eval':1}
            assert gate['status']=='awaiting_approval' and not gate['publishable']
            assert s.organization_policy('benchmark_strict')['gate']['status']=='blocked'
            with pytest.raises(ValueError,match='policy-compliant'):
                s.organization_review('person-mira','approved')
            assert s.organization_policy('research_review')['gate']['status']=='awaiting_approval'
            with pytest.raises(ValueError,match='policy-required role'):
                s.organization_review('person-arjun','approved')
            assert s.organization_review('person-mira','approved')['status']=='approved'
            assert s.organization_policy('benchmark_strict')['gate']['status']=='blocked'
            assert s.organization_policy('research_review')['gate']['status']=='approved'
            assert len(s.organization_context()['decision_sources'])==1
        finally:s.close()


def test_rejection_and_policy_binding(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'one')
        try:
            staged(s)
            assert s.organization_review('person-mira','rejected')['status']=='rejected'
            assert s.organization_policy('sandbox')['gate']['status']=='awaiting_approval' # unresolved claims still require review
            assert s.organization_policy('research_review')['gate']['status']=='rejected'
            assert s.organization_review('person-mira','approved')['status']=='approved'
            with pytest.raises(ValueError):s.organization_policy('arbitrary')
        finally:s.close()


def test_approval_survives_validated_checkpoint_and_is_not_carried_to_new_release(tmp_path):
    from holdout.checkpoints import CheckpointStore
    with service_modules() as service:
        cp=CheckpointStore(tmp_path/'checkpoints')
        s=service.HoldoutService(FIXTURE,tmp_path/'first',checkpoint_store=cp)
        staged(s)
        rid=s.run_id
        s.organization_review('person-mira','approved')
        s.close()
        recovered=service.HoldoutService(FIXTURE,tmp_path/'second',checkpoint_store=cp,resume_id=rid)
        try:
            assert recovered.release['verified']
            assert recovered.organization_context()['gate']['status']=='approved'
            assert recovered.organization_policy('benchmark_strict')['gate']['status']=='blocked'
            assert recovered.organization_policy('research_review')['gate']['status']=='approved'
        finally:recovered.close()
        fresh=service.HoldoutService(FIXTURE,tmp_path/'third')
        try:
            staged(fresh)
            assert fresh.organization_context()['gate']['status']=='awaiting_approval'
        finally:fresh.close()


def test_org_context_tampering_fails_closed(tmp_path):
    from holdout.organization import OrganizationBrain,OrganizationError
    from stage11_support import StoreAdapter
    source=ROOT/'fixtures/organization'
    copied=tmp_path/'organization'
    copied.mkdir()
    (copied/'organization.json').write_bytes((source/'organization.json').read_bytes())
    (copied/'policies').mkdir()
    for file in (source/'policies').iterdir():
        (copied/'policies'/file.name).write_bytes(file.read_bytes())
    graph=StoreAdapter()
    b=OrganizationBrain(graph,copied/'organization.json',tmp_path/'audit.json','local-run')
    assert b.active_policy()['id']=='research_review'
    (copied/'policies'/'ORG-DATA-07.md').write_text('This statement was changed')
    with pytest.raises(OrganizationError,match='source changed'):
        b.active_policy()


def test_local_review_rejects_out_of_scope_identity_and_release(tmp_path):
    with service_modules() as service:
        s=service.HoldoutService(FIXTURE,tmp_path/'run')
        try:
            with pytest.raises(ValueError, match='verified'):
                s.organization_review('person-mira','approved')
            staged(s)
            for name in ('unknown', 'person-arjun'):
                with pytest.raises(ValueError):s.organization_review(name,'approved')
            with pytest.raises(ValueError,match='Decision'):
                s.organization_review('person-mira','not_real')
        finally:s.close()
