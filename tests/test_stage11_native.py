"""Real engine, MCP, and restart acceptance. Run inside the built Docker image.

These tests are separate from the offline service contracts. No fake graph or
fallback solver is installed here. The tests make no model/provider calls.
"""
from pathlib import Path
import asyncio

import pytest

pytest.importorskip('falkordb')
pytest.importorskip('mcp')

from holdout.agent import deterministic
from holdout.checkpoints import CheckpointStore
from holdout.ingest import prepare_dataset
from holdout.service import HoldoutService, verify_release

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.native
@pytest.mark.parametrize('policy,cost,counts',[('answer_leakage',5,(1,0,4)),('exact_record',3,(3,2,0))])
def test_real_engine_mcp_and_restart(tmp_path,policy,cost,counts):
    inputs=tmp_path/'input'
    prepare_dataset(ROOT/'examples/stage1_1-review/train.jsonl',ROOT/'examples/stage1_1-review/eval.jsonl',inputs,separation_policy=policy)
    cp=CheckpointStore(tmp_path/'checkpoints')
    first=HoldoutService(inputs,tmp_path/'first',checkpoint_store=cp)
    try:
        receipt=asyncio.run(deterministic(first))
        assert receipt['verified'] and receipt['cost']==cost
        assert first.engine['native_maxflow'] is True
        assert tuple(first.compact_status()['counts'][k] for k in ('confirmed','rejected','unresolved'))==counts
        assert verify_release(first.release_dir,first.fixture)['checks']['release_review_matches']
        run_id=first.run_id;expected=receipt['review']
    finally:first.close()
    second=HoldoutService(inputs,tmp_path/'second',checkpoint_store=cp,resume_id=run_id)
    try:
        assert second.release['verified'] and second.release['review']==expected
        assert any(e['tool']=='session_resumed' for e in second.store.events())
    finally:second.close()
