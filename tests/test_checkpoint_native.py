"""Native checkpoint restore tests; require pinned FalkorDB engine.

Run after ``python -m holdout doctor --install-native-module`` on Linux.
"""
from pathlib import Path

import pytest

pytest.importorskip('falkordb')

from holdout.checkpoints import CheckpointError, CheckpointStore
from holdout.service import HoldoutService, PROJECT_ROOT


@pytest.mark.native
def test_replay_confirmed_and_exported_release_after_restart(tmp_path):
    checkpoint = CheckpointStore(tmp_path/'sessions')
    fixture = PROJECT_ROOT/'fixtures'/'demo'/'dataset.json'
    first = HoldoutService(fixture, tmp_path/'sessions'/'boot1', checkpoint_store=checkpoint)
    first.investigate('c401','exact')
    first.investigate('c403','normalized_ast')
    expected = first.state()
    first.stage_release()
    checkpoint.activate(first.run_id, first.catalog_path)
    run_id = first.run_id
    first.close()
    second = HoldoutService(fixture, tmp_path/'sessions'/'boot2',
                            checkpoint_store=checkpoint, resume_id=run_id)
    try:
        assert second.run_id == run_id
        assert second.state()['revision'] == expected['revision']
        assert second.candidate('c401')['status'] == 'confirmed'
        assert second.candidate('c403')['status'] == 'confirmed'
        assert second.release and second.release['verified'] is True
        assert second.agent['status'] == 'completed'
        assert second.artifact('train.jsonl')
    finally:
        second.close()


@pytest.mark.native
def test_changed_source_is_not_silently_restored(tmp_path):
    checkpoint = CheckpointStore(tmp_path/'sessions')
    from shutil import copytree
    source = PROJECT_ROOT/'fixtures'/'demo'
    fixture_dir = tmp_path/'snapshot'
    copytree(source,fixture_dir)
    fixture = fixture_dir/'dataset.json'
    first = HoldoutService(fixture,tmp_path/'sessions'/'boot1',checkpoint_store=checkpoint)
    first.investigate('c401','exact')
    checkpoint.activate(first.run_id,first.catalog_path)
    run_id = first.run_id
    first.close()
    a_record = fixture_dir/'records'/'t101.py'
    a_record.write_bytes(a_record.read_bytes() + b'\n# modified after checkpoint\n')
    with pytest.raises(CheckpointError, match='changed'):
        HoldoutService(fixture,tmp_path/'sessions'/'boot2',checkpoint_store=checkpoint,resume_id=run_id)
