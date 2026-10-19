"""Database-free policy, applicability, and review-contract regressions."""
import copy
import json
from pathlib import Path

import pytest

from holdout.evidence import load_fixture, compare_records, assess_lineage
from holdout.ingest import prepare_rows, prepare_dataset
from holdout.policies import separation_policy
from holdout.review import candidate_review, compare_record_identity, release_review

ROOT = Path(__file__).resolve().parents[1]
MULTI = 'def one(x):\n    return x + 1\n\ndef two(x):\n    return x * 2\n'
CLASS = 'class C:\n    def method(self):\n        return 5\n'


def make_pair(tmp_path, a, b=None, policy='answer_leakage', **row):
    if b is None:
        b = a
    prepare_rows([{'content':a, 'source_id':'shared', **row}],
                 [{'content':b, 'source_id':'shared', **row}],tmp_path/'fixture',separation_policy=policy)
    fixture = load_fixture(tmp_path/'fixture')
    return fixture, fixture['candidates'][0]


@pytest.mark.parametrize('content', [MULTI, CLASS, 'ordinary text'])
@pytest.mark.parametrize('policy,verdict', [('exact_record','confirmed'), ('answer_leakage','unresolved')])
def test_identical_content_is_not_automatically_answer_leakage(tmp_path, content, policy, verdict):
    f,c = make_pair(tmp_path,content,policy=policy)
    result = compare_record_identity(f,c['train_id'],c['eval_id'])
    assert result['verdict'] == verdict
    assert result['evidence'][0]['identical'] is True
    assert c['status'] == 'unresolved'  # Read-only inspection is not a graph write.


def test_exact_policy_rejects_different_contents_only_within_its_rule(tmp_path):
    f,c=make_pair(tmp_path,MULTI,MULTI+'\n',policy='exact_record')
    result=compare_record_identity(f,c['train_id'],c['eval_id'])
    assert result['verdict']=='rejected'
    assert 'independence has not been established' in result['reason']


def test_answer_policy_does_not_clear_a_mismatch(tmp_path):
    f,c=make_pair(tmp_path,'first note','second note')
    assert compare_record_identity(f,c['train_id'],c['eval_id'])['verdict']=='unresolved'


def test_content_hashes_are_recomputed_not_trusted(tmp_path):
    f,c=make_pair(tmp_path,MULTI)
    f['records'][0]['content_sha256']='0'*64
    r=compare_record_identity(f,c['train_id'],c['eval_id'])
    assert r['evidence'][0]['train_sha256']!='0'*64
    (Path(f['_root'])/f['records'][0]['content_path']).write_text(MULTI+'# changed')
    assert not compare_record_identity(f,c['train_id'],c['eval_id'])['evidence'][0]['identical']


@pytest.mark.parametrize('content',[MULTI,CLASS])
def test_unavailable_methods_are_explained(tmp_path,content):
    f,c=make_pair(tmp_path,content)
    r=candidate_review(f,c)
    assert not r['methods']['exact']['available']
    assert not r['methods']['normalized_ast']['available']
    assert r['methods']['exact']['reason']
    assert r['identity']['identical']
    assert 'Not established' in r['answer_relevance']


def test_explicit_scaffold_is_not_promoted_to_answer(tmp_path):
    f,c=make_pair(tmp_path,'def helper(x):\n    return x + 1\n',content_role='scaffold')
    r=candidate_review(f,c)
    assert not r['methods']['exact']['available']
    assert compare_record_identity(f,c['train_id'],c['eval_id'])['verdict']=='unresolved'


def test_explicit_symbols_enable_supported_comparisons(tmp_path):
    f,c=make_pair(tmp_path,MULTI,solution_symbols=['two'])
    r=candidate_review(f,c)
    assert r['methods']['exact']['available'] and r['methods']['normalized_ast']['available']
    assert compare_records(f,c['train_id'],c['eval_id'])['verdict']=='confirmed'
    assert 'explicitly' in r['answer_relevance']


def test_inferred_symbols_are_disclosed(tmp_path):
    f,c=make_pair(tmp_path,'def answer(x):\n    return x+9\n')
    assert 'inferred' in candidate_review(f,c)['answer_relevance']


def test_unsupported_ast_does_not_disable_exact(tmp_path):
    f,c=make_pair(tmp_path,'def answer(xs):\n    return [x+1 for x in xs]\n', solution_symbols=['answer'])
    r=candidate_review(f,c)
    assert r['methods']['exact']['available']
    assert not r['methods']['normalized_ast']['available']


def test_exact_policy_only_admits_identity_method(tmp_path):
    f,c=make_pair(tmp_path,MULTI,policy='exact_record',solution_symbols=['one','two'])
    r=candidate_review(f,c)
    assert r['pending_methods']==['record_identity']
    assert all(not r['methods'][name]['available'] for name in ['exact','normalized_ast','provenance'])


def test_exhausted_methods_not_proposed(tmp_path):
    f,c=make_pair(tmp_path,MULTI)
    r=candidate_review(f,c,{'record_identity','provenance'})
    assert r['pending_methods']==[]
    assert r['methods']['record_identity']['state']=='exhausted'


def test_no_provenance_links_makes_method_unavailable(tmp_path):
    f,c=make_pair(tmp_path,MULTI)
    f['links']=[]
    assert not candidate_review(f,c)['methods']['provenance']['available']


def test_bad_policy_fails_import_atomically(tmp_path):
    with pytest.raises(ValueError,match='separation_policy'):
        prepare_rows([{'content':'x'}],[{'content':'x'}],tmp_path/'bad',separation_policy='guess')
    assert not (tmp_path/'bad').exists()


def test_old_fixture_keeps_policy_and_metadata_unchanged():
    f=load_fixture(ROOT/'fixtures/demo')
    assert separation_policy(f)=='answer_leakage'
    assert 'separation_policy' not in f['policy']


def test_policy_frozen_at_import_independent_of_costs(tmp_path):
    f,c=make_pair(tmp_path,MULTI,policy='exact_record')
    assert f['policy']['separation_policy']=='exact_record'
    assert f['policy']['cost_policy']=='equal'
    assert all(r['cost']==1 for r in f['records'])
    assert f['import_summary']['separation_policy']=='exact_record'


def test_review_covers_unknowns_and_warns_on_empty_pool():
    records=[{'id':'t','split':'train','cost':1},{'id':'e','split':'eval','cost':1,'protected':True}]
    candidates=[{'id':'c','train_id':'t','eval_id':'e','status':'unresolved'}]
    r=release_review(records,candidates,{'kept':['e'],'excluded':['t']},'answer_leakage')
    assert r['by_split']['train']['retained']==0 and r['by_split']['eval']['retained']==1
    assert r['by_split']['eval']['protected_retained']==1
    assert r['conflicts']['unresolved_covered']==1
    assert r['unresolved'][0]['covered_by']==['t']
    assert r['warnings'][0]['code']=='empty_train_pool'
    assert r['balance_guarantee'] is False


def test_new_example_policy_outcomes(tmp_path):
    for policy, expected in [('answer_leakage',(1,0,4)),('exact_record',(3,2,0))]:
        output=tmp_path/policy
        prepare_dataset(ROOT/'examples/stage1_1-review/train.jsonl',ROOT/'examples/stage1_1-review/eval.jsonl',output,separation_policy=policy)
        f=load_fixture(output)
        assert len(f['candidates'])==5
        statuses=[]
        for c in f['candidates']:
            review=candidate_review(f,c)
            result=None
            for method in review['pending_methods']:
                if method=='record_identity':
                    result=compare_record_identity(f,c['train_id'],c['eval_id'])
                elif method in {'exact','normalized_ast'}:
                    result=compare_records(f,c['train_id'],c['eval_id'],method)
                else:
                    # Empty supplied paths cannot prove lineage. No graph is simulated here.
                    result=assess_lineage(f,c['train_id'],c['eval_id'],{}, {})
                if result['verdict']!='unresolved': break
            statuses.append(result['verdict'])
        assert tuple(statuses.count(s) for s in ['confirmed','rejected','unresolved'])==expected


def exhaustive(records,candidates):
    from itertools import combinations
    best=float('inf')
    ids=[r['id'] for r in records if not r['protected']]
    weights={r['id']:r['cost'] for r in records}
    for n in range(len(ids)+1):
        for selected in combinations(ids,n):
            s=set(selected)
            if all(c['train_id'] in s or c['eval_id'] in s for c in candidates if c['status']!='rejected'):
                best=min(best,sum(weights[i] for i in s))
    return best


def test_controlled_decision_impact_fixture_has_informative_and_redundant_unknowns():
    f=load_fixture(ROOT/'fixtures/decision-impact')
    for c in f['candidates']:
        if c['id'] in {'a-x','a-y','b-y'}:
            c['status']=compare_records(f,c['train_id'],c['eval_id'])['verdict']
            assert c['status']=='confirmed'
    assert exhaustive(f['records'],f['candidates'])==11
    high=copy.deepcopy(f['candidates']); next(c for c in high if c['id']=='high')['status']='rejected'
    redundant=copy.deepcopy(f['candidates']); next(c for c in redundant if c['id']=='redundant')['status']='rejected'
    assert exhaustive(f['records'],high)==3
    assert exhaustive(f['records'],redundant)==11
    # These are exact controlled counterfactual costs, not an implemented prioritizer.
