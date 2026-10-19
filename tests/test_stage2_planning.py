"""Offline contract tests. Explicit NetworkX adapter, NEVER native validation."""
from copy import deepcopy
from pathlib import Path
from itertools import product
import json
import random

import pytest
from stage11_support import service_modules, StoreAdapter, TestRevisionConflict as RevisionConflict

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/decision-impact/dataset.json'


@pytest.fixture
def service(tmp_path):
    with service_modules() as module:
        s = module.HoldoutService(FIXTURE, tmp_path / 'run')
        try:
            yield s
        finally:
            s.close()


def prime(s):
    for cid in ('a-x', 'a-y', 'b-y'):
        assert s.investigate(cid, 'exact')['status'] == 'confirmed'


def test_counterfactuals_rank_without_mutation(service):
    prime(service)
    snapshot = (service.store.records(), service.store.candidates(), service.store.revision(), deepcopy(service._attempts))
    plan = service.plan_investigation()
    assert plan['bounds']['lower']['cost'] == 3 and plan['bounds']['upper']['cost'] == 11
    assert plan['next_candidate']['id'] == 'high'
    rows = {r['candidate_id']: r for r in plan['ranked']}
    assert rows['high']['rejection']['saving'] == 8
    assert rows['high']['confirmation']['uplift'] == 8
    assert rows['redundant']['rejection']['saving'] == 0
    assert snapshot == (service.store.records(), service.store.candidates(), service.store.revision(), service._attempts)
    # Crucial: a cheap hypothetical cut cannot become an actual release.
    assert service.stage_release()['cost'] == 11
    assert service.candidate('high')['status'] == 'unresolved'


def test_real_evidence_closes_gap_and_preserves_unknown(service):
    prime(service)
    service.plan_investigation()
    before = service.store.revision()
    assert service.investigate('high', 'provenance', before)['status'] == 'rejected'
    assert service.state()['planning'] is None
    plan = service.plan_investigation()
    assert plan['stop']['code'] == 'cost_stable' and plan['stop']['stop']
    assert plan['work']['scenario_solves'] == 0
    assert service.candidate('redundant')['status'] == 'unresolved'
    assert service.stage_release()['cost'] == 3
    from holdout.planning import verify_plan
    assert verify_plan(service.planning_report(), service.fixture)['cost_stable']
    from holdout.optimizer import reference_solve
    for outcome in ('rejected', 'confirmed'):
        hypothetical = [{**c,'status':outcome} if c['id']=='redundant' else c for c in service.store.candidates()]
        assert reference_solve(service.store.records(), hypothetical, True)['cost'] == 3


def test_cached_plan_and_polling_do_not_rerun_native_scenarios(service, monkeypatch):
    service.plan_investigation()
    import holdout.planning as planning
    monkeypatch.setattr(planning, 'solve_counterfactual', lambda *a,**kw: pytest.fail('unexpected scenario'))
    assert service.plan_investigation()['plan_id'] == service.state()['planning']['plan_id']
    for _ in range(4):
        service.state(); service.compact_status()
    with pytest.raises(RevisionConflict):
        service.plan_investigation(expected_revision=999)


def test_partial_scan_is_not_global_ranking_or_stop(service):
    plan = service.plan_investigation(max_candidates=1)
    assert plan['coverage']['evaluated'] == 1
    assert not plan['coverage']['complete_actionable_scan']
    assert plan['coverage']['not_evaluated'] == 4
    assert not plan['stop']['stop']


def test_exhaustion_is_not_cost_stability(service):
    # Exhaust all methods without pretending an uncertainty is rejected.
    for cid in ('a-x', 'a-y', 'b-y'):
        service.investigate(cid, 'exact')
    service._attempts['high'] = {'record_identity','exact','normalized_ast','provenance'}
    service._attempts['redundant'] = {'record_identity','provenance'}
    plan = service.plan_investigation()
    assert plan['bounds']['gap'] == 8
    assert plan['stop']['code'] == 'evidence_exhausted'
    assert not plan['stop']['cost_stable']
    assert service.stage_release()['cost'] == 11


def test_zero_marginals_with_joint_uncertainty_do_not_stop(tmp_path):
    with service_modules():
        from holdout.planning import build_plan
        from holdout.optimizer import solve
        store=StoreAdapter()
        records=[{'id':name,'split':split,'cost':1,'protected':False} for name,split in [('a','train'),('b','train'),('x','eval'),('y','eval')]]
        cs=[{'id':'base','train_id':'a','eval_id':'x','status':'confirmed'}]
        cs += [{'id':cid,'train_id':t,'eval_id':e,'status':'unresolved'} for cid,t,e in [('u1','a','y'),('u2','a','y'),('u3','b','x'),('u4','b','x')]]
        store.seed({'records':records,'candidates':cs,'links':[],'entities':[]})
        bounds={'lower':solve(store,False),'upper':solve(store,True),'gap':1}
        plan=build_plan(store,{c['id']:{'pending_methods':['exact']} for c in cs if c['status']=='unresolved'},bounds,run_id='test',catalog_digest='test',policy='answer_leakage')
        assert all(r['rejection']['saving']==0 and r['confirmation']['uplift']==0 for r in plan['ranked'])
        assert not plan['stop']['stop'] and plan['next_candidate'] is not None
        assert plan['bounds']['gap']==1


def test_protected_pair_infeasibility_branches():
    with service_modules():
        from holdout.planning import build_plan
        from holdout.optimizer import solve
        store=StoreAdapter()
        records=[{'id':i,'split':s,'cost':1,'protected':True} for i,s in [('t','train'),('e','eval')]]
        c={'id':'u','train_id':'t','eval_id':'e','status':'unresolved'}
        store.seed({'records':records,'candidates':[c],'links':[],'entities':[]})
        def get_plan():
            bounds={'lower':solve(store,False),'upper':solve(store,True),'gap':None}
            return build_plan(store,{'u':{'pending_methods':['record_identity']}},bounds,run_id='test',catalog_digest='test',policy='exact_record')
        p=get_plan()
        assert not p['stop']['stop'] and p['ranked'][0]['rejection']['restores_feasibility']
        assert p['ranked'][0]['confirmation']['proves_infeasibility']
        assert p['ranked'][0]['rejection']['saving'] is None
        store.set_candidate('u','confirmed',{})
        p=get_plan()
        assert p['stop']['code']=='confirmed_infeasible'
        assert not p['stop']['cost_stable']


@pytest.mark.parametrize('mutation', ['saving','capacity','status','gap','score','stable','revision'])
def test_report_rejects_tampering(service, mutation):
    service.plan_investigation()
    from holdout.planning import verify_plan
    report=service.planning_report()
    assert verify_plan(report, service.fixture)['valid']
    cid=report['ranked'][0]['candidate_id']
    if mutation=='saving':report['ranked'][0]['rejection']['saving']+=1
    elif mutation=='capacity':report['proofs']['counterfactuals'][cid]['rejected']['certificate']['flow_edges'][0]['capacity']+=1
    elif mutation=='status':report['input_candidates'][0]['status']='confirmed'
    elif mutation=='gap':report['bounds']['gap']=0
    elif mutation=='score':report['ranked'][0]['score']+=1
    elif mutation=='stable':report['stop'].update(code='cost_stable',cost_stable=True,stop=True)
    elif mutation=='revision':report['binding']['revision']+=1
    with pytest.raises((ValueError, RuntimeError)):
        verify_plan(report, service.fixture)


def test_source_mutation_refuses_planning(service):
    service.catalog_sha256='changed'
    with pytest.raises(ValueError,match='changed'):
        service.plan_investigation()


def test_time_cap_before_scenario_is_partial_not_proof(service):
    from holdout.planning import build_plan
    readings=iter([0,100,100,100])
    p=build_plan(service.store,{c['id']:service.review_candidate(c) for c in service.store.candidates()},service.bounds(),
                 run_id=service.run_id,catalog_digest='test',policy='answer_leakage',clock=lambda:next(readings),max_seconds=1)
    assert p['coverage']['time_limited'] and p['coverage']['evaluated']==0
    assert not p['stop']['stop'] and p['next_candidate'] is None


def test_revision_change_during_solve_refuses_result(service, monkeypatch):
    import holdout.planning as planning
    actual=planning.solve_counterfactual
    def changed(*args,**kwargs):
        result=actual(*args,**kwargs)
        service.store.rev+=1
        return result
    monkeypatch.setattr(planning,'solve_counterfactual',changed)
    with pytest.raises(RevisionConflict):
        service.plan_investigation()
    assert service._plan_cache is None


def test_random_scenarios_match_exhaustive_optima():
    rng=random.Random(20261019)
    with service_modules():
        from holdout.optimizer import solve_counterfactual
        for _ in range(45):
            records=[{'id':str(i),'split':'train' if i<3 else 'eval','cost':rng.randint(0,8),'protected':rng.random()<.15} for i in range(6)]
            cs=[{'id':f'{a}-{b}','train_id':str(a),'eval_id':str(b),'status':rng.choice(['confirmed','unresolved','rejected'])} for a in range(3) for b in range(3,6) if rng.random()<.6]
            store=StoreAdapter();store.seed({'records':records,'candidates':cs,'links':[],'entities':[]})
            for c in cs:
                if c['status']!='unresolved':continue
                for outcome in ('confirmed','rejected'):
                    result=solve_counterfactual(store,c['id'],outcome,1)
                    changed=[{**x,'status':outcome} if x['id']==c['id'] else x for x in cs]
                    active=[x for x in changed if x['status'] in ({'confirmed','unresolved'} if outcome=='rejected' else {'confirmed'})]
                    feasible=[]
                    for mask in range(64):
                        excluded={str(i) for i in range(6) if mask>>i&1}
                        if any(r['protected'] and r['id'] in excluded for r in records):continue
                        if any(x['train_id'] not in excluded and x['eval_id'] not in excluded for x in active):continue
                        feasible.append(sum(r['cost'] for r in records if r['id'] in excluded))
                    assert result['feasible']==bool(feasible)
                    if feasible:assert result['cost']==min(feasible)


def test_report_accepts_database_record_sort_order(service):
    service.store._records.sort(key=lambda r:r['id'])
    service.plan_investigation()
    from holdout.planning import verify_plan
    assert verify_plan(service.planning_report(),service.fixture)['valid']


def test_paired_baseline_distractor_same_stopping_rule(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('benchmark_stage2', ROOT/'scripts/benchmark_stage2.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with service_modules() as svc:
        data=module.benchmark(svc.HoldoutService,ROOT/'fixtures/decision-impact-distractor/dataset.json',tmp_path,
                              prime_ids=('a-x','a-y','b-y','d-w'),budget=1)
        rows={r['strategy']:r for r in data}
        assert rows['graph_guided']['investigation_order']==['high']
        assert rows['graph_guided']['after']['upper']==23
        assert rows['endpoint_with_gap_stop']['investigation_order']==['e-w']
        assert rows['endpoint_with_gap_stop']['after']['upper']==31
        assert all(row['verified'] for row in data)


def test_report_budget_cannot_masquerade_as_proof(service, monkeypatch):
    import holdout.planning as planning
    monkeypatch.setattr(planning,'MAX_REPORT_BYTES',10)
    with pytest.raises(ValueError,match='report budget'):
        service.plan_investigation()
    assert service._plan_cache is None


def test_candidate_labels_are_not_used_as_truth_or_impact(service):
    prime(service)
    # Change candidate IDs only in the TEST store; production immutable input
    # changes require a new snapshot. No label such as "high" reaches scoring.
    for c in service.store._candidates:
        if c['id']=='high':c['id']='q-1042'
        elif c['id']=='redundant':c['id']='q-0001'
    plan=service.plan_investigation()
    assert plan['next_candidate']['id']=='q-1042'
    assert plan['ranked'][0]['rejection']['saving']==8
