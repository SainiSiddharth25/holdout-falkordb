"""Stage 6 offline watcher tests. No native FalkorDB or MCP assertions."""
from pathlib import Path
import json
import pytest

from holdout.sentinel import SentinelWatch, WatchError, observe_pair


def make_pair(folder, train=None, evaluation=None):
    folder.mkdir(exist_ok=True)
    train = train if train is not None else [{'id':'a','content':'def solve(x): return x + 1','source_id':'r1'}]
    evaluation = evaluation if evaluation is not None else [{'id':'b','content':'def solve(x): return x + 1','source_id':'r1'}]
    for file, rows in [('train', train),('eval', evaluation)]:
        (folder / f'{file}.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))


def test_watch_detects_modified_removed_added_without_mutating_release(tmp_path):
    source=tmp_path/'watch'; make_pair(source)
    monitor=SentinelWatch(source,tmp_path/'state')
    initial=monitor.snapshot(observe_pair(source)['digest'])
    assert monitor.status()['state']=='pending'
    monitor.commit(initial,'aabbccddeeff','equal','answer_leakage')
    assert monitor.status(active_run_id='aabbccddeeff')['state']=='current'
    make_pair(source, train=[{'id':'a','content':'changed'},{'id':'c','content':'added'}], evaluation=[{'id':'d','content':'new'}])
    state=monitor.scan()
    assert state['state']=='pending' and state['stale_release'] is False # no active_run_id in scan()
    changed=monitor.status(active_run_id='aabbccddeeff')
    assert changed['stale_release']
    assert changed['delta']['added']==2 and changed['delta']['removed']==1 and changed['delta']['modified']==1
    assert changed['delta']['affected_record_keys']==['eval:b','eval:d','train:a','train:c']
    assert changed['events'][-1]['kind']=='change_detected'
    with pytest.raises(WatchError,match='changed since preview'):
        monitor.snapshot(initial['digest'])


def test_watch_restart_persists_only_fingerprints_not_raw_data(tmp_path):
    source=tmp_path/'watch';make_pair(source)
    monitor=SentinelWatch(source,tmp_path/'state')
    snap=monitor.snapshot(observe_pair(source)['digest'])
    monitor.commit(snap,'aabbccddeeff','balanced','exact_record')
    saved=(tmp_path/'state'/'sentinel-watch.json').read_text()
    assert 'def solve' not in saved and 'source_id' not in saved
    recovered=SentinelWatch(source,tmp_path/'state')
    assert recovered.status(active_run_id='aabbccddeeff')['bound_to_active_run']
    make_pair(source,train=[{'id':'a','content':'different'}])
    assert recovered.scan()['state']=='pending'
    again=SentinelWatch(source,tmp_path/'state')
    assert again.status(active_run_id='aabbccddeeff')['stale_release']


def test_missing_malformed_and_linked_inputs_fail_closed(tmp_path):
    watch=tmp_path/'watch';make_pair(watch)
    monitor=SentinelWatch(watch,tmp_path/'state')
    base=monitor.snapshot(observe_pair(watch)['digest'])
    monitor.commit(base,'aabbccddeeff','equal','answer_leakage')
    (watch/'eval.jsonl').write_text('invalid-json')
    status=monitor.scan()
    assert status['state']=='unavailable' and status['error']
    (watch/'eval.jsonl').unlink()
    (watch/'eval.jsonl').symlink_to(watch/'train.jsonl')
    assert monitor.scan()['state']=='unavailable'
    with pytest.raises(WatchError):monitor.snapshot(base['digest'])


def test_duplicate_id_and_oversize_rejected(tmp_path):
    watch=tmp_path/'watch';make_pair(watch)
    (watch/'train.jsonl').write_text(''.join(json.dumps({'id':'a','content':'x'})+'\n' for _ in range(2)))
    with pytest.raises(WatchError,match='duplicate'):
        observe_pair(watch)
    (watch/'train.jsonl').write_bytes(b'z'*(4*1024*1024+1))
    with pytest.raises(WatchError,match='larger'):
        observe_pair(watch)


def test_watch_promotion_changes_active_run_and_blocks_stale_release(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from stage11_support import service_modules
    source=tmp_path/'watch-inputs'
    make_pair(source)
    import sys
    from types import ModuleType
    agent_stub = ModuleType('holdout.agent')
    agent_stub.run_agent = lambda *_: None  # API behavior only, no model invocation
    monkeypatch.setitem(sys.modules, 'holdout.agent', agent_stub)
    sys.modules.pop('holdout.api',None)
    (tmp_path/'dist').mkdir(exist_ok=True)
    with service_modules():
        import holdout.api as api
        monkeypatch.setattr(api, 'PROJECT_ROOT', tmp_path)
        app=api.create_app()
        with TestClient(app) as client:
            before=client.get('/api/state').json()
            preview=client.post('/api/sentinel/scan').json()
            assert preview['state']=='pending'
            start=client.post('/api/sentinel/promote',json={
                'expected_digest':preview['latest_digest'],
                'expected_run_id':before['run_id'],'policy':'equal','separation_policy':'exact_record'})
            assert start.status_code==200, start.text
            active=start.json()
            assert active['run_id']!=before['run_id']
            assert active['sentinel_watch']['bound_to_active_run']
            assert active['sentinel_watch']['state']=='current'
            assert active['candidates']
            assert client.post('/api/release',json={'conservative':True}).status_code==200
            make_pair(source, train=[{'id':'a','content':'def solve(x): return x + 2','source_id':'r1'}])
            changed=client.post('/api/sentinel/scan').json()
            assert changed['stale_release']
            assert changed['delta']['modified']==1
            assert changed['affected_candidate_count']>=1
            assert client.post('/api/release',json={'conservative':True}).status_code==400
            assert client.get('/api/artifacts/repair.json').status_code==400
            assert client.post('/api/organization/review',json={'actor_id':'person-mira','decision':'approved'}).status_code==400
            assert client.post('/api/sentinel/promote',json={
                'expected_digest':preview['latest_digest'],'expected_run_id':active['run_id']}).status_code==400
            changed_digest=changed['latest_digest']
            nxt=client.post('/api/sentinel/promote',json={
                'expected_digest':changed_digest,'expected_run_id':active['run_id'],'policy':'equal','separation_policy':'exact_record'})
            assert nxt.status_code==200, nxt.text
            assert nxt.json()['run_id']!=active['run_id']
            assert nxt.json()['release'] is None
            assert nxt.json()['organization']['gate']['status']=='not_staged'


def test_watch_rejects_stale_run_id_and_bad_policy(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from stage11_support import service_modules
    make_pair(tmp_path/'watch-inputs')
    import sys
    from types import ModuleType
    agent_stub = ModuleType('holdout.agent')
    agent_stub.run_agent = lambda *_: None  # API behavior only, no model invocation
    monkeypatch.setitem(sys.modules, 'holdout.agent', agent_stub)
    sys.modules.pop('holdout.api',None)
    (tmp_path/'dist').mkdir(exist_ok=True)
    with service_modules():
        import holdout.api as api
        monkeypatch.setattr(api, 'PROJECT_ROOT', tmp_path)
        with TestClient(api.create_app()) as client:
            digest=client.post('/api/sentinel/scan').json()['latest_digest']
            assert client.post('/api/sentinel/promote',json={'expected_digest':digest,'expected_run_id':'a'*12}).status_code==409
            run=client.get('/api/state').json()['run_id']
            assert client.post('/api/sentinel/promote',json={'expected_digest':digest,'expected_run_id':run,'policy':'unknown'}).status_code==400
            assert client.get('/api/state').json()['run_id']==run
