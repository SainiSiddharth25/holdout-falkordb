"""Stage 3 promotion checks: genuine native FalkorDB + SDK MCP, no fake engine.

Run inside the packaged Docker container. No model/API key is accessed.
"""
import asyncio
from pathlib import Path
import pytest
pytest.importorskip('falkordb')
pytest.importorskip('mcp')

from holdout.service import HoldoutService
from holdout.checkpoints import CheckpointStore
from holdout.mcp_server import local_client, result_dict

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/decision-impact/dataset.json'

@pytest.mark.native
def test_memory_relationships_sdk_recall_and_procedural_hints(tmp_path):
    s=HoldoutService(FIXTURE,tmp_path/'one')
    try:
        assert s.store.memories() == []
        before=s.store.revision()
        s.investigate('a-x','exact')
        assert s.store.revision() == before+1
        graph=s.store._query("""MATCH (m:Memory)-[:ABOUT]->(c:Candidate {id:'a-x'}),
                                       (m)-[:SUPPORTED_BY]->(r:EvidenceReceipt)
                               RETURN m.id,m.kind,r.id""")
        assert len(graph)==3
        assert {row[1] for row in graph} == {'episodic','semantic','procedural'}
        async def check_mcp():
            async with local_client(s) as client:
                tools={tool.name for tool in (await client.list_tools()).tools}
                assert {'recall_memory','suggest_procedures'} <= tools
                history=result_dict(await client.call_tool('recall_memory',{'candidate_id':'a-x'}))
                assert history['count']==3
                hint=result_dict(await client.call_tool('suggest_procedures',{'candidate_id':'a-y'}))
                assert any('exact' in suggestion['suggested_methods'] for suggestion in hint['suggestions'])
        asyncio.run(check_mcp())
        assert s.candidate('a-y')['status']=='unresolved'
        assert s.store.revision()==before+1
    finally:s.close()

@pytest.mark.native
def test_restart_revalidates_memory_and_cannot_restore_unchecked_note(tmp_path):
    cp=CheckpointStore(tmp_path/'checkpoint')
    s=HoldoutService(FIXTURE,tmp_path/'one',checkpoint_store=cp)
    s.investigate('a-x','exact')
    before={m['id'] for m in s.recall_memories()['items']}
    rid=s.run_id;s.close()
    s=HoldoutService(FIXTURE,tmp_path/'two',checkpoint_store=cp,resume_id=rid)
    try:
        assert {m['id'] for m in s.recall_memories()['items']}==before
        assert s.memory_overview()['current_total']==3
        assert s.store.revision()==2
        # A graph-only mutation of memory prose cannot pass content validation.
        memory=s.store.memories()[0]
        from copy import deepcopy
        forged=deepcopy(memory)
        forged['content']['note']='Unverified model claim'  # not source-backed
        s.store._query("MATCH (m:Memory {id:$id}) SET m.data=$data",{
            'id': forged['id'], 'data': __import__('json').dumps(forged,sort_keys=True)})
        assert s.recall_memories()['count']==2
        # But candidate verdict and repair are unchanged by memory tampering.
        assert s.candidate('a-x')['status']=='confirmed'
    finally:s.close()
