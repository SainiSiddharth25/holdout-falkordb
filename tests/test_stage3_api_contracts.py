"""Offline Stage 3 HTTP read contract, with explicitly fake graph adapter."""
from pathlib import Path
from fastapi.testclient import TestClient
from stage11_support import service_modules
from stage2_support import runner_module
from test_stage2_contracts import api_module

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'fixtures/decision-impact/dataset.json'


def test_memory_api_read_only_and_no_untrusted_writes(tmp_path):
    with service_modules() as service, runner_module():
        s=service.HoldoutService(FIXTURE,tmp_path/'run')
        try:
            with api_module() as api, TestClient(api.create_app(service=s)) as client:
                assert client.get('/api/memory').json()['count']==0
                s.investigate('a-x','exact')
                revision=s.store.revision()
                data=client.get('/api/memory?candidate_id=a-x&kind=semantic').json()
                assert data['count']==1
                assert data['items'][0]['content']['finding'].startswith('Candidate a-x is confirmed')
                assert data['items'][0]['validity']=='current'
                suggestions=client.get('/api/memory/suggestions/a-y').json()
                assert suggestions['candidate_id']=='a-y'
                assert 'exact' in suggestions['suggestions'][0]['suggested_methods']
                assert s.candidate('a-y')['status']=='unresolved'
                assert s.store.revision()==revision
                assert client.get('/api/memory?kind=made_up').status_code==400
                assert client.get('/api/memory?limit=1000').status_code==400
                assert client.get('/api/memory?candidate_id=unknown').status_code==404
                assert client.post('/api/memory',json={'candidate_id':'a-y','status':'confirmed'}).status_code in {404,405}
        finally:s.close()
