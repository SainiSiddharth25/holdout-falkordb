"""Stage 4 native promotion gate: actual FalkorDB, SDK MCP and verified release."""
import asyncio
from pathlib import Path
import pytest
pytest.importorskip('falkordb')
pytest.importorskip('mcp')
from holdout.service import HoldoutService, verify_release
from holdout.mcp_server import local_client, result_dict
from holdout.agent import coordinated
from holdout.graph import RevisionConflict
from holdout.checkpoints import CheckpointStore

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/decision-impact/dataset.json'

@pytest.mark.native
def test_native_claim_handoff_and_receipt(tmp_path):
    s=HoldoutService(FIXTURE,tmp_path/'run')
    try:
        assert len(s.store.tasks())==5
        async def main():
            async with local_client(s) as client:
                tools={t.name for t in (await client.list_tools()).tools}
                assert {'claim_investigation','finish_investigation','coordination_status'} <= tools
                claim=result_dict(await client.call_tool('claim_investigation',
                       {'role':'code','owner':'code-worker'}))['claim']
                assert claim['candidate_id']=='a-x'
                with pytest.raises(RevisionConflict):
                    s.investigate('a-x','exact')
                result=result_dict(await client.call_tool('investigate_candidate',
                       {'candidate_id':'a-x','method':'exact','lease_token':claim['lease_token']}))
                assert result['status']=='confirmed'
                result_dict(await client.call_tool('finish_investigation',
                       {'candidate_id':'a-x','lease_token':claim['lease_token']}))
                assert s.store.task('a-x')['state']=='done'
                with pytest.raises(RevisionConflict):
                    s.finish_investigation('a-x',claim['lease_token'])
        asyncio.run(main())
    finally:s.close()

@pytest.mark.native
def test_two_mcp_specialists_complete_verified_release(tmp_path):
    s=HoldoutService(FIXTURE,tmp_path/'run')
    try:
        release=asyncio.run(coordinated(s))
        assert release['verified'] is True
        assert all(t['state']=='done' for t in s.store.tasks())
        assert any(e['tool']=='task_handoff' for e in s.store.events())
        assert s.release_dir and verify_release(s.release_dir,s.fixture)['verified']
        assert s.agent['status']=='completed' and s.agent['mode']=='coordinated'
    finally:s.close()

@pytest.mark.native
def test_recovery_invalidates_old_token(tmp_path):
    cp=CheckpointStore(tmp_path/'checkpoint')
    s=HoldoutService(FIXTURE,tmp_path/'a',checkpoint_store=cp)
    claim=s.claim_investigation('code','specialist')['claim']
    s.investigate('a-x','exact',lease_token=claim['lease_token'])
    rid=s.run_id;s.close()
    s=HoldoutService(FIXTURE,tmp_path/'b',checkpoint_store=cp,resume_id=rid)
    try:
        assert s.candidate('a-x')['status']=='confirmed'
        assert not any(t['state']=='leased' for t in s.store.tasks())
        with pytest.raises(RevisionConflict):
            s.finish_investigation('a-x',claim['lease_token'])
    finally:s.close()

@pytest.mark.native
def test_native_claims_are_exclusive_for_concurrent_workers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    s=HoldoutService(FIXTURE,tmp_path/'run')
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            claims=list(pool.map(lambda n:s.claim_investigation('code',f'worker-{n}')['claim'],range(3)))
        assert all(claims)
        assert len({c['candidate_id'] for c in claims})==3
        assert len({c['lease_token'] for c in claims})==3
        assert all(s.store.task(c['candidate_id'])['state']=='leased' for c in claims)
        for c in claims:
            with pytest.raises(RevisionConflict):
                s.finish_investigation(c['candidate_id'],'0'*32)
    finally:s.close()
