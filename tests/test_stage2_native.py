"""Promotion gate: actual native FalkorDB and SDK MCP. Never a solver fallback.

Run inside Docker. No provider/API-key calls. These tests intentionally do NOT
fall back to the service-contract adapters on missing dependencies or failures.
"""
import asyncio
from pathlib import Path
import pytest
pytest.importorskip('falkordb')
pytest.importorskip('mcp')

from holdout.service import HoldoutService,verify_release
from holdout.agent import graph_guided
from holdout.checkpoints import CheckpointStore
from holdout.mcp_server import local_client,result_dict
from holdout.planning import verify_plan

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/decision-impact/dataset.json'


@pytest.mark.native
def test_real_mcp_counterfactuals_and_no_mutation(tmp_path):
    s=HoldoutService(FIXTURE,tmp_path/'run')
    try:
        for cid in ('a-x','a-y','b-y'):s.investigate(cid,'exact')
        before=(s.store.revision(),s.store.records(),s.store.candidates())
        async def scenario():
            async with local_client(s) as client:
                assert 'plan_investigation' in {t.name for t in (await client.list_tools()).tools}
                p=result_dict(await client.call_tool('plan_investigation',{'expected_revision':s.store.revision()}))
                assert p['next_candidate']['id']=='high'
                assert p['ranked'][0]['rejection']['saving']==8
                return p
        p=asyncio.run(scenario())
        assert before==(s.store.revision(),s.store.records(),s.store.candidates())
        assert s.engine['native_maxflow']
        report=s.planning_report()
        assert verify_plan(report,s.fixture)['valid']
        assert s.stage_release()['cost']==11  # hypothetical 3 is not exportable
        s.investigate('high','provenance')
        assert s.plan_investigation()['stop']['cost_stable']
        assert s.stage_release()['cost']==3
    finally:s.close()


@pytest.mark.native
def test_real_guided_run_and_restart(tmp_path):
    cp=CheckpointStore(tmp_path/'checkpoint')
    s=HoldoutService(FIXTURE,tmp_path/'one',checkpoint_store=cp)
    try:
        receipt=asyncio.run(graph_guided(s))
        assert receipt['verified'] and receipt['cost']==3
        assert s.agent['investigation_stop']=='cost_stable'
        assert s.candidate('redundant')['status']=='unresolved'
        assert verify_release(s.release_dir,s.fixture)['verified']
        run=s.run_id
    finally:s.close()
    s=HoldoutService(FIXTURE,tmp_path/'two',checkpoint_store=cp,resume_id=run)
    try:
        assert s.release['verified'] and s.release['cost']==3
        assert s.state()['planning'] is None
        assert s.plan_investigation()['stop']['cost_stable']
        assert verify_plan(s.planning_report(),s.fixture)['original_evidence_replayed']
    finally:s.close()


@pytest.mark.native
def test_native_distractor_equal_budget_baseline(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('stage2_benchmark',ROOT/'scripts/benchmark_stage2.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    results=module.benchmark(HoldoutService,ROOT/'fixtures/decision-impact-distractor/dataset.json',tmp_path,
                             prime_ids=('a-x','a-y','b-y','d-w'),budget=1)
    rows={r['strategy']:r for r in results}
    assert rows['graph_guided']['after']['upper']==23
    assert rows['endpoint_with_gap_stop']['after']['upper']==31
    assert all(row['verified'] and row['engine']['native_maxflow'] for row in results)
