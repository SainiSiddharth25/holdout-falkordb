"""Offline service/HTTP/runner contracts. No native engine, MCP or provider."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from stage11_support import service_modules
from stage2_support import runner_module

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/decision-impact/dataset.json'


@pytest.fixture
def modules():
    with service_modules() as service, runner_module() as agent:
        yield service,agent


def test_graph_guided_runner_stages_and_stops_at_stability(modules,tmp_path):
    service,agent=modules
    s=service.HoldoutService(FIXTURE,tmp_path/'run')
    try:
        r=asyncio.run(agent.graph_guided(s))
        assert r['verified'] and r['cost']==3
        assert s.agent['status']=='completed' and s.agent['mode']=='graph_guided'
        assert s.agent['investigation_stop']=='cost_stable'
        assert s.candidate('high')['status']=='rejected'
        assert s.candidate('redundant')['status']=='unresolved'
        assert s.candidate('a-y')['status']=='unresolved'
        assert s.agent['steps']==4
        assert s.planning_report()['stop']['cost_stable']
        assert service.verify_release(s.release_dir,s.fixture)['verified']
    finally:s.close()


def test_budget_stop_still_exports_conservative_not_false_completion(modules,tmp_path):
    service,agent=modules
    s=service.HoldoutService(FIXTURE,tmp_path/'run')
    try:
        r=asyncio.run(agent.graph_guided(s,max_investigations=1))
        assert r['verified'] and r['cost']==3
        assert s.agent['investigation_stop']=='investigation_budget'
        assert s.bounds()['gap']==3
        assert 'not proof' in s.agent['message']
        assert not s.agent.get('usage',{}).get('provider_calls',0)
    finally:s.close()


def test_legacy_exhaustive_runner_remains_available(modules,tmp_path):
    service,agent=modules
    s=service.HoldoutService(FIXTURE,tmp_path/'run')
    try:
        r=asyncio.run(agent.deterministic(s))
        assert r['verified'] and r['cost']==3
        assert s.agent['steps']==5 and s.agent['mode']=='deterministic'
        assert s.candidate('a-y')['status']=='confirmed'
    finally:s.close()


def test_checkpoint_resume_discards_plan_not_evidence(modules,tmp_path):
    service,_=modules
    from holdout.checkpoints import CheckpointStore
    cp=CheckpointStore(tmp_path/'checkpoints')
    s=service.HoldoutService(FIXTURE,tmp_path/'one',checkpoint_store=cp)
    for cid in ['a-x','a-y','b-y']:s.investigate(cid,'exact')
    old=s.plan_investigation()['plan_id'];run=s.run_id;s.close()
    s=service.HoldoutService(FIXTURE,tmp_path/'two',checkpoint_store=cp,resume_id=run)
    try:
        assert s.state()['planning'] is None
        assert all(s.candidate(cid)['status']=='confirmed' for cid in ['a-x','a-y','b-y'])
        assert s.plan_investigation()['next_candidate']['id']=='high'
        s.investigate('high','provenance');s.stage_release();s.close()
        s=service.HoldoutService(FIXTURE,tmp_path/'three',checkpoint_store=cp,resume_id=run)
        assert s.release['verified'] and s.release['cost']==3
        assert s.plan_investigation()['stop']['cost_stable']
    finally:s.close()


@contextmanager
def api_module():
    previous=sys.modules.pop('holdout.api',None)
    import holdout
    old_attr=getattr(holdout,'api',None)
    try:yield importlib.import_module('holdout.api')
    finally:
        sys.modules.pop('holdout.api',None)
        if previous:sys.modules['holdout.api']=previous
        if old_attr is not None:setattr(holdout,'api',old_attr)
        elif hasattr(holdout,'api'):delattr(holdout,'api')


def test_http_plan_revision_download_and_controls(modules,tmp_path):
    service,_=modules
    s=service.HoldoutService(FIXTURE,tmp_path/'run')
    try:
        with api_module() as api, TestClient(api.create_app(service=s)) as client:
            assert client.get('/api/plan/report').status_code==400
            state=client.get('/api/state').json()
            response=client.post('/api/plan',json={'expected_revision':state['revision'],'max_candidates':2})
            assert response.status_code==200,response.text
            p=response.json()['planning'];assert len(p['ranked'])==2
            assert 'proofs' not in p and 'input_records' not in p
            report=client.get('/api/plan/report')
            assert report.status_code==200 and 'counterfactuals' in report.json()['proofs']
            assert 'attachment' in report.headers['content-disposition']
            assert client.post('/api/plan',json={'max_candidates':129}).status_code==422
            assert client.post('/api/plan',json={'max_candidates':True}).status_code==422
            assert client.post('/api/plan',json={'set_verdict':'rejected'}).status_code==422
            assert client.post('/api/plan',json={},headers={'Origin':'https://evil.invalid'}).status_code==403
            revision=state['revision']
            assert client.post('/api/investigate',json={'candidate_id':'a-x','method':'exact'}).status_code==200
            assert client.post('/api/plan',json={'expected_revision':revision}).status_code==409
            assert client.get('/api/plan/report').status_code==400
            s.agent['status']='running'
            assert client.post('/api/plan',json={}).status_code==409
            assert client.post('/api/examples/decision-impact',json={}).status_code==409
            s.agent['status']='idle'
            result=client.post('/api/examples/decision-impact',json={})
            assert result.status_code==200
            assert result.json()['run_id']!=state['run_id']
            assert all(c['status']=='unresolved' for c in result.json()['candidates'])
            api_service=client.app.state.holdout['service'];api_service.close()
    finally:s.close()


def test_api_graph_guided_worker_completes(modules,tmp_path):
    import time
    service,_=modules
    s=service.HoldoutService(FIXTURE,tmp_path/'run')
    try:
        with api_module() as api,TestClient(api.create_app(service=s)) as client:
            assert client.post('/api/run',json={'mode':'graph_guided'}).status_code==200
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                state=client.get('/api/state').json()
                if state['agent']['status']!='running':break
                time.sleep(.01)
            assert state['agent']['status']=='completed'
            assert state['release']['verified'] and state['release']['cost']==3
            assert state['planning']['stop']['cost_stable']
    finally:s.close()
