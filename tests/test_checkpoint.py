"""Pure persistence tests requiring no FalkorDB or model credentials."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from holdout.checkpoints import CheckpointError, CheckpointStore, evidence_signature


def _example(method='exact', verdict='confirmed'):
    return {'id':'c1', 'status': verdict, 'evidence': {
        'verdict':verdict, 'method':method, 'attempts':[
            {'method':method, 'verdict':verdict, 'evidence':{'match':True},
             'graph_provenance':{'train':{'revision':5}}}
        ]}}


def _save(storage, candidates=None):
    storage.save(run_id='abc123abc123', catalog_path=storage.workspace/'data.json',
                 catalog_digest='digest', fingerprints={'records/a.py':'sha'},
                 candidates=candidates or [_example()], release_staged=False)


def test_checkpoint_and_active_pointer_are_restart_safe(tmp_path):
    first = CheckpointStore(tmp_path)
    _save(first)
    first.activate('abc123abc123', tmp_path/'data.json')
    restored = CheckpointStore(tmp_path)
    value = restored.current()
    assert value['run_id']=='abc123abc123'
    assert value['decisions'][0]['verdict']=='confirmed'
    assert value['fingerprints']=={'records/a.py':'sha'}
    assert (tmp_path/'active-session.json').stat().st_mode & 0o077 == 0
    assert not list((tmp_path/'checkpoints').glob('*.tmp'))


def test_new_release_requires_checkpoint_before_pointer(tmp_path):
    store = CheckpointStore(tmp_path)
    with pytest.raises(CheckpointError):
        store.activate('abc123abc123', tmp_path/'data.json')
    assert store.current() is None


def test_mismatched_pointer_fails_closed(tmp_path):
    store = CheckpointStore(tmp_path)
    _save(store)
    store.activate('abc123abc123', tmp_path/'data.json')
    (tmp_path/'active-session.json').write_text(json.dumps({
        'schema_version':1,'run_id':'abc123abc123','catalog_path':'/wrong'}))
    with pytest.raises(CheckpointError, match='disagrees'):
        store.current()


def test_tampered_checkpoint_fails_closed(tmp_path):
    store = CheckpointStore(tmp_path)
    _save(store)
    store.activate('abc123abc123', tmp_path/'data.json')
    (tmp_path/'checkpoints'/'abc123abc123.json').write_text('{broken')
    with pytest.raises(CheckpointError, match='decoded'):
        store.current()


def test_ignores_reconstruction_revision_only(tmp_path):
    one = _example()['evidence']
    two = json.loads(json.dumps(one))
    two['attempts'][0]['graph_provenance']['train']['revision']=99
    assert evidence_signature(one)==evidence_signature(two)
    two['attempts'][0]['evidence']['match']=False
    assert evidence_signature(one)!=evidence_signature(two)


def test_untrusted_methods_rejected(tmp_path):
    store = CheckpointStore(tmp_path)
    with pytest.raises(CheckpointError, match='Unknown'):
        _save(store, candidates=[_example(method='shell')])


def test_new_decision_replaces_atomic_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path)
    _save(store)
    _save(store, candidates=[_example(verdict='rejected')])
    assert store.load('abc123abc123')['decisions'][0]['verdict']=='rejected'
