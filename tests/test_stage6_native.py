"""Stage 6 actual FalkorDB/API integration. Run inside the documented Docker image."""
import json
from pathlib import Path
import pytest
pytest.importorskip('falkordb')
pytest.importorskip('mcp')
from fastapi.testclient import TestClient

from holdout.sentinel import observe_pair


def _put(folder, train_code='def answer(x): return x + 1'):
    folder.mkdir(exist_ok=True)
    records = ({'id':'t','content':train_code}, {'id':'e','content':'def answer(x): return x + 1'})
    for split, row in [('train',records[0]),('eval',records[1])]:
        (folder/f'{split}.jsonl').write_text(json.dumps(row)+'\n')


@pytest.mark.native
def test_native_watch_drives_graph_and_blocks_stale_release(tmp_path,monkeypatch):
    import holdout.api as api
    watch_dir=tmp_path/'watch-inputs'
    _put(watch_dir)
    (tmp_path/'dist').mkdir()
    monkeypatch.setattr(api,'PROJECT_ROOT',tmp_path)
    with TestClient(api.create_app()) as client:
        start=client.get('/api/state').json()
        digest=client.post('/api/sentinel/scan').json()['latest_digest']
        res=client.post('/api/sentinel/promote',json={'expected_run_id':start['run_id'],
            'expected_digest':digest,'separation_policy':'exact_record','policy':'equal'})
        assert res.status_code==200,res.text
        record=res.json()
        assert record['candidates'] and record['sentinel_watch']['state']=='current'
        output=client.post('/api/release',json={'conservative':True})
        assert output.status_code==200 and output.json()['release']['verified']
        _put(watch_dir, train_code='def answer(x): return x + 10')
        drift=client.post('/api/sentinel/scan').json()
        assert drift['affected_candidate_count'] == 1
        stale=client.get('/api/state').json()
        assert stale['release']['stale_watched_source']
        assert stale['organization']['gate']['status']=='stale_input'
        assert client.post('/api/release',json={'conservative':True}).status_code==400
        assert client.get('/api/artifacts/repair.json').status_code==400
        fresh=client.post('/api/sentinel/promote',json={'expected_run_id':record['run_id'],
            'expected_digest':drift['latest_digest'],'separation_policy':'exact_record','policy':'equal'})
        assert fresh.status_code==200 and fresh.json()['release'] is None
        assert fresh.json()['run_id']!=record['run_id']
        assert fresh.json()['organization']['gate']['status']=='not_staged'

@pytest.mark.native
def test_native_watch_recovers_promotion_after_restart(tmp_path,monkeypatch):
    import holdout.api as api
    watch_dir=tmp_path/'watch-inputs';_put(watch_dir)
    (tmp_path/'dist').mkdir()
    monkeypatch.setattr(api,'PROJECT_ROOT',tmp_path)
    with TestClient(api.create_app()) as client:
        rid=client.get('/api/state').json()['run_id']
        sha=client.get('/api/sentinel').json()['latest_digest']
        promoted=client.post('/api/sentinel/promote',json={'expected_run_id':rid,'expected_digest':sha,'policy':'equal'}).json()
        rid=promoted['run_id']
        client.post('/api/release',json={'conservative':True})
    with TestClient(api.create_app()) as client:
        saved=client.get('/api/state').json()
        assert saved['run_id']==rid
        assert saved['release']['verified']
        assert saved['sentinel_watch']['bound_to_active_run']
        _put(watch_dir, train_code='def answer(x): return x + 100')
        assert client.post('/api/sentinel/scan').json()['stale_release']
