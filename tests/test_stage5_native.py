"""Native Stage 5 promotion checks; not executed in offline-only source creation."""
import asyncio
from pathlib import Path
import pytest
pytest.importorskip('falkordb')
pytest.importorskip('mcp')
from holdout.service import HoldoutService
from holdout.mcp_server import local_client,result_dict
from holdout.checkpoints import CheckpointStore

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/decision-impact/dataset.json'

@pytest.mark.native
def test_native_company_graph_and_mcp_context(tmp_path):
    s=HoldoutService(FIXTURE,tmp_path/'engine')
    try:
        context=s.organization_context()
        assert context['gate']['status']=='not_staged'
        assert len(context['datasets_and_owners'])==2
        assert len(s.store._query('MATCH (d:OrgDataset)-[:OWNED_BY]->(o:OrgPerson) RETURN d.id,o.id'))==2
        assert len(s.store._query('MATCH (p:OrgPolicy)-[:JUSTIFIED_BY]->(d:OrgDecision) RETURN p.id,d.id'))==3
        async def check():
            async with local_client(s) as client:
                names={t.name for t in (await client.list_tools()).tools}
                assert 'organization_context' in names
                response=result_dict(await client.call_tool('organization_context',{}))
                assert response['gate']['status']=='not_staged'
        asyncio.run(check())
    finally:s.close()

@pytest.mark.native
def test_native_verified_repair_approval_and_policy_block(tmp_path):
    s=HoldoutService(FIXTURE,tmp_path/'engine')
    try:
        for candidate in s.store.candidates():s.investigate(candidate['id'])
        assert s.stage_release()['verified']
        assert s.organization_context()['gate']['status']=='awaiting_approval'
        assert s.organization_policy('benchmark_strict')['gate']['status']=='blocked'
        assert s.organization_policy('research_review')['gate']['status']=='awaiting_approval'
        with pytest.raises(ValueError):s.organization_review('person-arjun','approved')
        assert s.organization_review('person-mira','approved')['status']=='approved'
        assert len(s.store._query('MATCH (a:OrgApproval)-[:ASSESSED_UNDER]->(p:OrgPolicy) RETURN a.id,p.id'))==1
    finally:s.close()

@pytest.mark.native
def test_native_governance_recovered_with_replayed_evidence(tmp_path):
    cp=CheckpointStore(tmp_path/'checkpoint')
    s=HoldoutService(FIXTURE,tmp_path/'first',checkpoint_store=cp)
    try:
        for c in s.store.candidates():s.investigate(c['id'])
        s.stage_release();rid=s.run_id
        assert s.organization_review('person-mira','approved')['status']=='approved'
    finally:s.close()
    again=HoldoutService(FIXTURE,tmp_path/'second',checkpoint_store=cp,resume_id=rid)
    try:
        assert again.release['verified']
        assert again.organization_context()['gate']['status']=='approved'
    finally:again.close()
