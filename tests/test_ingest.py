"""v2 importer tests intentionally need no model or database service."""
from pathlib import Path
import json
import pytest

from holdout.ingest import ImportError as HoldoutImportError, prepare_dataset, prepare_rows
from holdout.evidence import load_fixture, compare_records


ROOT = Path(__file__).resolve().parents[1]


def test_automatic_candidate_discovery_and_policy(tmp_path):
    catalog = prepare_dataset(ROOT/'examples/v2-import/train.jsonl', ROOT/'examples/v2-import/eval.jsonl',
                              tmp_path/'fixture', policy='eval_first')
    fixture = load_fixture(tmp_path/'fixture')
    assert len(fixture['records']) == 5
    assert len(fixture['candidates']) == 2
    assert {r['cost'] for r in fixture['records'] if r['split']=='train'} == {1}
    assert {r['cost'] for r in fixture['records'] if r['split']=='eval'} == {10}
    assert sum(r['protected'] for r in fixture['records']) == 1
    candidates = { (c['train_id'], c['eval_id']): c for c in fixture['candidates'] }
    renamed = candidates[('t000001','e000001')]
    assert renamed['status'] == 'unresolved'
    assert 'ast' in renamed['discovery_signals']
    assert compare_records(fixture, renamed['train_id'], renamed['eval_id'], 'normalized_ast')['verdict'] == 'confirmed'
    same_metadata = candidates[('t000003','e000002')]
    assert same_metadata['discovery_signals'] == ['shared_source_reference']
    assert same_metadata['status'] == 'unresolved'
    assert fixture['links'] and all('evidence_path' not in link for link in fixture['links'])
    assert all(entity['kind'] == 'unverified_source_reference' for entity in fixture['entities'])
    assert catalog['policy']['weights']['eval'] == 10


def test_equal_policy_and_no_presupplied_candidates(tmp_path):
    catalog = prepare_rows([{'content':'Hello world'}],[{'content':'Hello world'}], tmp_path/'equal')
    assert len(catalog['candidates']) == 1
    assert all(r['cost']==1 for r in catalog['records'])
    assert all(c['status'] == 'unresolved' for c in catalog['candidates'])


def test_csv_and_order_independent_original_ids(tmp_path):
    (tmp_path/'tr.csv').write_text('id,content,protected\nA,"def f(x):\n    return x + 2",true\n')
    (tmp_path/'ev.csv').write_text('id,content\nA,"def f(x):\n    return x + 2"\n')
    catalog = prepare_dataset(tmp_path/'tr.csv', tmp_path/'ev.csv',tmp_path/'out')
    assert {r['original_id'] for r in catalog['records']} == {'A'}
    assert catalog['records'][0]['protected'] is True
    assert catalog['candidates']


def test_invalid_inputs_leave_no_partial_output(tmp_path):
    with pytest.raises(HoldoutImportError):
        prepare_rows([{'content': 'ok'}],[{'content': ''}],tmp_path/'broken')
    assert not (tmp_path/'broken').exists()
    with pytest.raises(HoldoutImportError):
        prepare_rows([{'id':'x', 'content':'abc'},{'id':'x','content':'def'}],[{'content':'a'}],tmp_path/'dup')
    with pytest.raises(HoldoutImportError):
        prepare_rows([{'content':'a'}],[{'content':'b'}],tmp_path/'weird', policy='not-a-policy')
    with pytest.raises(HoldoutImportError):
        prepare_rows([{'content':'a'}],[{'content':'b'}],tmp_path/'weird2', max_candidates=-1)


def test_output_is_immutable_and_paths_do_not_escape(tmp_path):
    output = tmp_path/'dataset'
    prepare_rows([{'content':'hello'}],[{'content':'world'}],output)
    with pytest.raises(HoldoutImportError):
        prepare_rows([{'content':'other'}],[{'content':'new'}],output)
    record = json.loads((output/'dataset.json').read_text())['records'][0]
    assert record['content_path'].startswith('records/t')
    assert not Path(record['content_path']).is_absolute()


def test_import_fingerprints_do_not_assume_provenance_proof(tmp_path):
    c = prepare_rows([{'content':'def h(x):\n    return x + 1\n','source_id':'ABC'}],
                     [{'content':'def j(y):\n    return y * 2\n','source_id':'ABC'}],tmp_path/'fixture')
    assert len(c['candidates']) == 1
    assert c['candidates'][0]['status'] == 'unresolved'
    assert 'shared_source_reference' in c['candidates'][0]['discovery_signals']
