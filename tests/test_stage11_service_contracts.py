"""Offline service contract tests. No native FalkorDB or MCP claim is made."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from holdout.checkpoints import CheckpointStore, CheckpointError
from holdout.ingest import prepare_dataset, prepare_rows
from stage11_support import service_modules

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def service_api():
    with service_modules() as service:
        yield service


def imported(tmp_path,policy='answer_leakage'):
    out=tmp_path/'input'
    prepare_dataset(ROOT/'examples/stage1_1-review/train.jsonl',ROOT/'examples/stage1_1-review/eval.jsonl',out,separation_policy=policy)
    return out


def complete(service):
    seen=0
    while (item:=service.next_candidate()) is not None:
        service.investigate(item['id'],'auto');seen+=1
        assert seen < 100, 'Auto investigation must exhaust methods rather than loop.'
    return service.stage_release()


def test_legacy_demo_decisions_and_costs_unchanged(service_api,tmp_path):
    s=service_api.HoldoutService(runtime_dir=tmp_path/'runtime')
    try:
        receipt=complete(s)
        assert receipt['verified'] and receipt['cost']==22
        assert s.compact_status()['counts']=={'confirmed':5,'rejected':1,'unresolved':2}
        assert s.bounds()['lower']['cost']==10
        assert receipt['checks']['release_review_matches']
    finally:s.close()


@pytest.mark.parametrize('policy,cost,counts',[('answer_leakage',5,(1,0,4)),('exact_record',3,(3,2,0))])
def test_import_investigate_export_and_verify_policy(service_api,tmp_path,policy,cost,counts):
    s=service_api.HoldoutService(imported(tmp_path,policy),tmp_path/'runtime')
    try:
        receipt=complete(s)
        assert receipt['verified'] and receipt['cost']==cost
        assert tuple(s.compact_status()['counts'][k] for k in ['confirmed','rejected','unresolved'])==counts
        assert receipt['separation_policy']==policy
        assert sum(x['retained'] for x in receipt['review']['by_split'].values())==len(receipt['kept'])
        assert service_api.verify_release(s.release_dir,s.fixture)['verified']
    finally:s.close()


def test_exhausted_auto_is_idempotent(service_api,tmp_path):
    s=service_api.HoldoutService(imported(tmp_path),tmp_path/'runtime')
    try:
        s.investigate('c000001')
        rev=s.store.revision();count=len(s.store.events())
        again=s.investigate('c000001')
        assert again['status']=='unresolved'
        assert s.store.revision()==rev and len(s.store.events())==count
        methods=[a['method'] for a in again['evidence']['attempts']]
        assert methods==['record_identity']  # No functions or graph links; skip unsupported work.
        assert s.candidate_detail('c000001')['review']['pending_methods']==[]
    finally:s.close()


def test_explicit_unavailable_call_cannot_change_verdict(service_api,tmp_path):
    s=service_api.HoldoutService(imported(tmp_path),tmp_path/'runtime')
    try:
        rev=s.store.revision()
        with pytest.raises(ValueError,match='unavailable'):
            s.investigate('c000001','normalized_ast')
        assert s.store.revision()==rev
    finally:s.close()


def test_separate_calls_preserve_evidence_history_for_restart(service_api,tmp_path):
    s=service_api.HoldoutService(imported(tmp_path),tmp_path/'runtime')
    try:
        s.investigate('c000004','record_identity')
        s.investigate('c000004','exact')
        result=s.investigate('c000004','normalized_ast')
        assert result['status']=='confirmed'
        assert [a['method'] for a in result['evidence']['attempts']]==['record_identity','exact','normalized_ast']
        s.stage_release()
        assert service_api.verify_release(s.release_dir,s.fixture)['verified']
    finally:s.close()


@pytest.mark.parametrize('policy',['answer_leakage','exact_record'])
def test_restart_replays_new_method_sequences(service_api,tmp_path,policy):
    inputs=imported(tmp_path,policy); cp=CheckpointStore(tmp_path/'sessions')
    a=service_api.HoldoutService(inputs,tmp_path/'r1',checkpoint_store=cp)
    expected=complete(a);run_id=a.run_id;a.close()
    b=service_api.HoldoutService(inputs,tmp_path/'r2',checkpoint_store=cp,resume_id=run_id)
    try:
        assert b.release['verified'] and b.release['cost']==expected['cost']
        assert b.release['review']==expected['review']
        assert any(e['tool']=='session_resumed' for e in b.store.events())
    finally:b.close()


def test_legacy_checkpoint_replays_recorded_sequence_not_new_auto(service_api,tmp_path):
    cp=CheckpointStore(tmp_path/'sessions')
    a=service_api.HoldoutService(runtime_dir=tmp_path/'r1',checkpoint_store=cp)
    # Emulate the exact Stage 1 method sequence for an unresolved original claim.
    a._restoring=True
    a.investigate('c408',_replay_methods=['exact','normalized_ast','provenance'])
    a._restoring=False;a._save_checkpoint();run_id=a.run_id;a.close()
    b=service_api.HoldoutService(runtime_dir=tmp_path/'r2',checkpoint_store=cp,resume_id=run_id)
    try:
        assert [a['method'] for a in b.candidate('c408')['evidence']['attempts']]==['exact','normalized_ast','provenance']
    finally:b.close()


@pytest.mark.parametrize('what',['review','policy','verdict','manifest'])
def test_release_tampering_detected(service_api,tmp_path,what):
    s=service_api.HoldoutService(imported(tmp_path,'exact_record'),tmp_path/'runtime')
    try:
        complete(s)
        report_path=s.release_dir/'repair.json';report=json.loads(report_path.read_text())
        if what=='review': report['review']['by_split']['train']['retained']+=1
        if what=='policy': report['separation_policy']='answer_leakage'
        if what=='verdict': report['candidates'][0]['evidence']['attempts'][0]['evidence'][0]['identical']=False
        if what=='manifest': (s.release_dir/'train.jsonl').write_text('')
        report_path.write_text(json.dumps(report))
        with pytest.raises(ValueError):service_api.verify_release(s.release_dir,s.fixture)
    finally:s.close()


def test_exact_policy_protected_duplicate_is_infeasible(service_api,tmp_path):
    out=tmp_path/'input';row={'content':'an identical protected record','protected':True}
    prepare_rows([row],[row],out,separation_policy='exact_record')
    s=service_api.HoldoutService(out,tmp_path/'runtime')
    try:
        s.investigate('c000001')
        assert s.bounds()['upper']['feasible'] is False
        with pytest.raises(ValueError,match='infeasible'):s.stage_release()
    finally:s.close()


def test_empty_pool_warning_visible_before_and_after_export(service_api,tmp_path):
    out=tmp_path/'input'
    prepare_rows([{'content':'duplicate'}],[{'content':'duplicate','protected':True}],out,separation_policy='exact_record')
    s=service_api.HoldoutService(out,tmp_path/'runtime')
    try:
        assert s.state()['repair_review']['warnings'][0]['code']=='empty_train_pool'
        receipt=complete(s)
        assert receipt['review']['by_split']['train']['retained']==0
        assert receipt['checks']['release_review_matches'] is True
    finally:s.close()


def test_policy_edit_rejected_during_run_and_restart(service_api,tmp_path):
    out=imported(tmp_path);cp=CheckpointStore(tmp_path/'sessions')
    s=service_api.HoldoutService(out,tmp_path/'runtime',checkpoint_store=cp)
    s.investigate('c000001');run_id=s.run_id
    catalog=out/'dataset.json';doc=json.loads(catalog.read_text());doc['policy']['separation_policy']='exact_record';catalog.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='changed'):s.stage_release()
    s.close()
    with pytest.raises(CheckpointError,match='changed'):
        service_api.HoldoutService(out,tmp_path/'r2',checkpoint_store=cp,resume_id=run_id)


def test_new_receipt_cannot_drop_review(service_api,tmp_path):
    s=service_api.HoldoutService(imported(tmp_path),tmp_path/'runtime')
    try:
        complete(s);path=s.release_dir/'repair.json';report=json.loads(path.read_text())
        assert report['schema_version']==2
        del report['review'];path.write_text(json.dumps(report))
        with pytest.raises(ValueError,match='require'):service_api.verify_release(s.release_dir,s.fixture)
    finally:s.close()


def test_legacy_bundled_receipt_still_verifies(service_api):
    from holdout.evidence import load_fixture
    result=service_api.verify_release(ROOT/'examples/verified-release',load_fixture(ROOT/'fixtures/demo'))
    assert result['verified'] and result['cost']==22


def test_review_reason_tamper_rejected(service_api,tmp_path):
    s=service_api.HoldoutService(imported(tmp_path),tmp_path/'runtime')
    try:
        complete(s);path=s.release_dir/'repair.json';report=json.loads(path.read_text())
        for c in report['candidates']:
            if c['status']=='unresolved':
                c['evidence']['attempts'][-1]['reason']='Fake reviewed claim'
                c['evidence']['reason']='Fake reviewed claim'
        path.write_text(json.dumps(report))
        with pytest.raises(ValueError,match='replay'):service_api.verify_release(s.release_dir,s.fixture)
    finally:s.close()


def test_http_import_policy_and_unknown_mode_rejection(service_api,tmp_path,monkeypatch):
    import importlib,sys
    from types import ModuleType
    from fastapi.testclient import TestClient
    saved_api=sys.modules.pop('holdout.api',None)
    agent=ModuleType('holdout.agent');agent.run_agent=lambda *args: None
    monkeypatch.setitem(sys.modules,'holdout.agent',agent)
    api=importlib.import_module('holdout.api')
    s=service_api.HoldoutService(runtime_dir=tmp_path/'runtime')
    try:
        with TestClient(api.create_app(service=s)) as client:
            same={'content':'class Counter:\n    pass\n'}
            response=client.post('/api/import',json={'train':[same],'evaluation':[same],'separation_policy':'exact_record'})
            assert response.status_code==200,response.text
            assert response.json()['separation_policy']['id']=='exact_record'
            assert client.post('/api/investigate',json={'candidate_id':'c000001','method':'normalized_ast'}).status_code==400
            assert client.post('/api/investigate',json={'candidate_id':'c000001'}).status_code==200
            detail=client.get('/api/candidates/c000001').json()
            assert detail['review']['identity']['identical'] is True
            assert detail['review']['policy_verdict']=='confirmed'
            release=client.post('/api/release',json={}).json()['release']
            assert release['review']['warnings'] and release['verified']
            before=client.get('/api/state').json()['run_id']
            assert client.post('/api/import',json={'train':[same],'evaluation':[same],'separation_policy':'invented'}).status_code==400
            assert client.get('/api/state').json()['run_id']==before
            assert client.post('/api/release',json={},headers={'Origin':'https://not-allowed.example'}).status_code==403
    finally:
        s.close()
        sys.modules.pop('holdout.api',None)
        if saved_api:sys.modules['holdout.api']=saved_api
