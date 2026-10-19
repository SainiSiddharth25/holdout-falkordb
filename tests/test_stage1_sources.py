"""Stage 1 source snapshot and captured provenance tests; no FalkorDB needed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

from holdout.git_sources import snapshot_git
from holdout.ingest import ImportError as ImportFailure, prepare_dataset, prepare_rows
from holdout.evidence import load_fixture, assess_lineage, compare_records, inspect_entity


def _git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.name', 'Test Author')
    _git(repo, 'config', 'user.email', 'tester@example.org')
    (repo / 'module.py').write_text('def add(x):\n    return x + 1\n')
    (repo / 'notes.txt').write_text('ignored')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'first')
    early = _git(repo, 'rev-parse', 'HEAD')
    (repo / 'module.py').write_text('def add(x):\n    return x + 2\n')
    (repo / 'other.py').write_text('def other(y):\n    return y * 2\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'second')
    return repo, early


def test_snapshots_contain_checked_git_blobs_and_bounded_history(tmp_path):
    repo, early = _repo(tmp_path)
    result = snapshot_git(repo, tmp_path / 'train.jsonl', repo_label='example', history_depth=3)
    assert result['records'] == 2
    rows = [json.loads(line) for line in (tmp_path/'train.jsonl').read_text().splitlines()]
    core = next(row for row in rows if 'module.py' in row['title'])
    assert len(core['git_snapshot']['history']) == 2
    assert core['git_snapshot']['history'][-1]['commit'] == early
    assert core['git_snapshot']['sha256'] == hashlib.sha256(core['content'].encode()).hexdigest()
    assert core['git_snapshot']['history'][0]['content'] == core['content']
    output = tmp_path/'eval.jsonl'
    snapshot_git(repo, output, revision=early, repo_label='example')
    catalog = prepare_dataset(tmp_path/'train.jsonl', output, tmp_path/'fixture')
    fixture = load_fixture(tmp_path/'fixture')
    assert catalog['import_summary']['captured_git_versions'] >= 2
    assert any(entity['kind']=='captured_git_blob' for entity in fixture['entities'])
    assert any(c['discovery_signals']==['shared_source_reference'] for c in fixture['candidates'])
    candidate = next(c for c in fixture['candidates'] if c['discovery_signals']==['shared_source_reference'])
    assert candidate['status'] == 'unresolved'
    assert compare_records(fixture, candidate['train_id'], candidate['eval_id'], 'exact')['verdict']=='unresolved'
    # Historical Git references do not magically become verified solution-transfer paths.
    assert assess_lineage(fixture, candidate['train_id'], candidate['eval_id'], [], [])['verdict']=='unresolved'
    git_entity = next(e['id'] for e in fixture['entities'] if e['kind']=='captured_git_blob')
    assert inspect_entity(fixture, git_entity)['available']


def test_git_snapshot_rejects_symlinks_and_limits(tmp_path):
    repo, _ = _repo(tmp_path)
    (repo/'link.py').symlink_to('module.py')
    _git(repo, 'add', 'link.py')
    _git(repo, 'commit', '-qm', 'link')
    with pytest.raises(ImportFailure, match='exceeds limit'):
        snapshot_git(repo, tmp_path/'no.jsonl', limit=1)
    assert not (tmp_path/'no.jsonl').exists()
    result = snapshot_git(repo, tmp_path/'yes.jsonl', limit=2)
    assert result['records']==2  # symlink excluded
    with pytest.raises(ImportFailure, match='exists'):
        snapshot_git(repo, tmp_path/'yes.jsonl')
    with pytest.raises(ImportFailure, match='revision'):
        snapshot_git(repo, tmp_path/'bad.jsonl', revision='--exec=something')
    with pytest.raises(ImportFailure, match='label'):
        snapshot_git(repo, tmp_path/'bad2.jsonl', repo_label='../bad')


def test_import_rejects_altered_git_objects_without_partial_state(tmp_path):
    repo, _ = _repo(tmp_path)
    snapshot_git(repo, tmp_path/'git.jsonl')
    data = [json.loads(x) for x in (tmp_path/'git.jsonl').read_text().splitlines()]
    data[0]['git_snapshot']['history'][0]['content'] += ' # tampered'
    with pytest.raises(ImportFailure, match='hash'):
        prepare_rows(data,[{'content':'def f(x):\n return x\n'}],tmp_path/'bad')
    assert not (tmp_path/'bad').exists()
    data = [json.loads(x) for x in (tmp_path/'git.jsonl').read_text().splitlines()]
    data[0]['git_snapshot']['blob_oid'] = '0' * 40
    with pytest.raises(ImportFailure, match='hash'):
        prepare_rows(data,[{'content':'def f(x):\n return x\n'}],tmp_path/'bad2')
    assert not (tmp_path/'bad2').exists()


def test_parquet_import_is_optional_with_clear_missing_dependency(tmp_path, monkeypatch):
    path = tmp_path/'records.parquet'
    path.write_bytes(b'not actual parquet')
    import builtins
    real_import = builtins.__import__
    def no_arrow(name, *args, **kwargs):
        if name=='pyarrow.parquet' or name=='pyarrow':
            raise ModuleNotFoundError('No module named pyarrow')
        return real_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins, '__import__', no_arrow)
    with pytest.raises(ImportFailure, match='requires pyarrow'):
        prepare_dataset(path, path, tmp_path/'fixture')


def test_parquet_rows_through_bounded_batch_reader(tmp_path, monkeypatch):
    path = tmp_path/'rows.parquet'
    path.write_bytes(b'fake for stub')
    class Batch:
        def to_pylist(self):
            return [{'id':'A','content':'def f(x):\n    return x+1\n'}]
    class File:
        metadata=types.SimpleNamespace(num_rows=1, num_row_groups=1, row_group=lambda i:types.SimpleNamespace(total_byte_size=64))
        def iter_batches(self, batch_size):
            assert batch_size==128
            yield Batch()
    pq=types.ModuleType('pyarrow.parquet');pq.ParquetFile=lambda p:File()
    pkg=types.ModuleType('pyarrow');pkg.parquet=pq
    monkeypatch.setitem(sys.modules,'pyarrow',pkg)
    monkeypatch.setitem(sys.modules,'pyarrow.parquet',pq)
    fixture = prepare_dataset(path,path,tmp_path/'fixture')
    assert fixture['import_summary']['record_count']==2
    assert len(fixture['candidates'])==1


def test_live_is_primary_and_deterministic_is_secondary():
    html=(Path(__file__).resolve().parents[1]/'dist/index.html').read_text()
    assert '<button id="llm-button" class="button primary"' in html
    assert '<button id="run-button" class="button secondary"' in html
    assert html.index('id="llm-button"') < html.index('id="run-button"')
