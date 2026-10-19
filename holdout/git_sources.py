"""Read-only, bounded snapshot of tracked Python files and their Git history.

Git object IDs identify captured file versions. They do not certify that an
answer was preserved between versions, or establish contamination.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from .ingest import ImportError as ImportFailure, MAX_CONTENT_BYTES, MAX_FILE_BYTES, MAX_RECORDS_PER_SPLIT

MAX_HISTORY_DEPTH = 12
MAX_GIT_OUTPUT = 8 * 1024 * 1024
LABEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')


def _git(repo: Path, *args: str, max_bytes: int = MAX_GIT_OUTPUT) -> bytes:
    try:
        result = subprocess.run(
            ['git', '-C', str(repo), *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImportFailure('Git could not read the repository snapshot.') from exc
    if result.returncode != 0:
        raise ImportFailure('Git could not read the specified revision or file.')
    if len(result.stdout) > max_bytes:
        raise ImportFailure('Git output exceeds the bounded snapshot limit.')
    return result.stdout


def _commit(repo: Path, revision: str) -> str:
    if not revision or len(revision) > 160 or revision.startswith('-') or any(c.isspace() for c in revision):
        raise ImportFailure('Invalid Git revision.')
    value = _git(repo, 'rev-parse', '--verify', f'{revision}^{{commit}}').decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
        raise ImportFailure('Git did not provide a supported object ID.')
    return value


def _text_blob(repo: Path, blob: str) -> str | None:
    size = int(_git(repo, 'cat-file', '-s', blob).decode('ascii').strip())
    if size > MAX_CONTENT_BYTES:
        return None
    raw = _git(repo, 'cat-file', 'blob', blob, max_bytes=MAX_CONTENT_BYTES)
    if len(raw) != size:
        raise ImportFailure('Git object contents changed while reading snapshot.')
    # Git verifies the blob object on read. Rehash using this repository's format.
    # In addition, preserve portable SHA-256 for an independent snapshot check.
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def snapshot_git(repo_path: str | Path, output_file: str | Path, *, revision: str = 'HEAD',
                 pattern: str = '*.py', limit: int = 250, history_depth: int = 4,
                 repo_label: str | None = None) -> dict[str, Any]:
    """Write a JSONL input for `holdout prepare`, without executing Git hooks.

    This only reads an existing local repository. No clone, network request,
    code import, test execution or working-tree content is involved.
    """
    repo = Path(repo_path).resolve(strict=True)
    if not repo.is_dir():
        raise ImportFailure('Git repository path must be a directory.')
    if type(limit) is not int or not (1 <= limit <= MAX_RECORDS_PER_SPLIT):
        raise ImportFailure('Git record limit must be between 1 and 5000.')
    if type(history_depth) is not int or not (1 <= history_depth <= MAX_HISTORY_DEPTH):
        raise ImportFailure('Git history depth must be between 1 and 12.')
    label = repo_label or repo.name
    if not LABEL_RE.fullmatch(label):
        raise ImportFailure('Repository label must use letters, numbers, . _ or -.')
    if not pattern or len(pattern) > 160 or '..' in pattern or '\x00' in pattern:
        raise ImportFailure('Invalid file pattern.')
    commit = _commit(repo, revision)
    raw_tree = _git(repo, 'ls-tree', '-rz', '--full-tree', commit)
    selected: list[tuple[str, str]] = []
    for entry in raw_tree.split(b'\x00'):
        if not entry:
            continue
        try:
            meta, raw_path = entry.split(b'\t', 1)
            mode, kind, blob = meta.decode('ascii').split(' ')
            path = raw_path.decode('utf-8')
        except (ValueError, UnicodeDecodeError) as exc:
            raise ImportFailure('Unexpected Git tree encoding.') from exc
        if mode not in ('100644', '100755') or kind != 'blob':
            continue  # never follow symlinks, submodules, or special objects
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
            selected.append((path, blob))
    if len(selected) > limit:
        raise ImportFailure(f'{len(selected)} matching Git files exceeds limit {limit}; narrow --pattern.')
    if not selected:
        raise ImportFailure('No matching tracked regular files at the specified revision.')
    output = Path(output_file).absolute()
    if output.exists() or output.is_symlink():
        raise ImportFailure('Snapshot destination exists; use a new output filename.')
    if output.suffix.lower() != '.jsonl':
        raise ImportFailure('Git snapshot output must end in .jsonl.')
    rows = []
    for path, blob in selected:
        content = _text_blob(repo, blob)
        if content is None or not content.strip():
            continue
        history = []
        # --full-history can be expensive; bounded `git log` touches only this
        # file's available same-path history and does not infer renames.
        history_bytes = _git(repo, 'log', f'-n{history_depth}', '--format=%H', commit, '--', path)
        ancestors = history_bytes.decode('ascii').splitlines()
        if commit not in ancestors:
            ancestors.insert(0, commit)
        for old_commit in ancestors[:history_depth]:
            if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', old_commit):
                continue
            # literal path rev lookup via tree entries, avoiding revision:path ambiguity
            version_entries = _git(repo, 'ls-tree', '-rz', '--full-tree', old_commit, '--', path)
            for entry in version_entries.split(b'\x00'):
                if not entry:
                    continue
                try:
                    meta, raw_path = entry.split(b'\t', 1)
                    mode, kind, version_blob = meta.decode('ascii').split(' ')
                except ValueError:
                    continue
                if raw_path != path.encode() or mode not in ('100644', '100755') or kind != 'blob':
                    continue
                version_text = _text_blob(repo, version_blob)
                if version_text is None:
                    continue
                history.append({
                    'commit': old_commit, 'blob_oid': version_blob,
                    'content': version_text,
                    'sha256': hashlib.sha256(version_text.encode('utf-8')).hexdigest(),
                })
                break
        if not history or history[0]['blob_oid'] != blob:
            history.insert(0, {'commit': commit, 'blob_oid': blob,
                               'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()})
        rows.append({
            'id': f'{label}:{path}@{commit[:12]}',
            'title': f'{label}/{path}',
            'content': content,
            'source_id': f'gitfile:{label}:{path}',
            'git_snapshot': {'repo_label': label, 'path': path, 'commit': commit,
                             'blob_oid': blob, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
                             'history': history},
        })
    if not rows:
        raise ImportFailure('Matching files were too large, empty, or not UTF-8.')
    payload = ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows).encode('utf-8')
    if len(payload) > MAX_FILE_BYTES:
        raise ImportFailure('Git snapshot exceeds 40 MiB. Narrow the selection.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(payload)
    return {'output': str(output), 'records': len(rows), 'revision': commit,
            'repo_label': label, 'sha256': hashlib.sha256(payload).hexdigest(),
            'history_depth': history_depth, 'skipped': len(selected) - len(rows),
            'note': 'Verified captured Git blobs; history is same-path only and does not prove solution preservation.'}
