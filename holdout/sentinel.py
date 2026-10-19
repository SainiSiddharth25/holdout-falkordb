"""Stage 6: bounded, local-only continuous dataset observation.

A watch observes two allowlisted files. It NEVER imports, modifies a graph,
invalidates a mathematical receipt, or approves a release automatically.
Promotion is an explicit revision/digest-checked action through the service.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import threading

from .ingest import POLICIES, MAX_RECORDS_PER_SPLIT
from .policies import SEPARATION_POLICIES

MAX_WATCH_BYTES = 4 * 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class WatchError(ValueError):
    pass


def _read_file(path: Path) -> tuple[list[dict], str]:
    """Reject symlinks, oversized files, malformed records, duplicate ids."""
    try:
        # O_NOFOLLOW closes the gap between preflight checks and the actual
        # read if another local process replaces the watched path.
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_WATCH_BYTES:
                raise WatchError('Watch inputs must be regular JSONL files no larger than 4 MiB.')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(MAX_WATCH_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_WATCH_BYTES:
            raise WatchError('Watch input exceeds the 4 MiB limit.')
    except FileNotFoundError as exc:
        raise WatchError('Put train.jsonl and eval.jsonl in the watched folder.') from exc
    except OSError as exc:
        raise WatchError('Watched source is unreadable or is a symbolic link.') from exc
    try:
        text = data.decode('utf-8')
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise WatchError('Watch input is not valid UTF-8 JSONL.') from exc
    if not (1 <= len(rows) <= MAX_RECORDS_PER_SPLIT) or any(not isinstance(x, dict) for x in rows):
        raise WatchError('Each watched split requires 1–5000 JSON object records.')
    ids = [str(x.get('id', i + 1)) for i, x in enumerate(rows)]
    if len(ids) != len(set(ids)):
        raise WatchError('Watched source contains duplicate record IDs.')
    return rows, _sha(data)


def observe_pair(folder: Path) -> dict:
    """Return a validated snapshot; no arbitrary caller-supplied path or URL."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise WatchError('Watched directory does not exist or is a symbolic link.')
    train, train_hash = _read_file(folder / 'train.jsonl')
    evaluation, eval_hash = _read_file(folder / 'eval.jsonl')
    digest = _sha(_canonical({'train': train_hash, 'eval': eval_hash}))
    rows = {}
    for split, items in (('train', train), ('eval', evaluation)):
        for index, row in enumerate(items):
            key = f'{split}:{row.get("id", index + 1)}'
            rows[key] = _sha(_canonical(row))
    return {'digest': digest, 'hashes': {'train': train_hash, 'eval': eval_hash},
            'records': rows, 'train': train, 'evaluation': evaluation}


def diff_rows(previous: dict, current: dict) -> dict:
    before, after = previous.get('records', {}), current['records']
    if not previous:
        return {'added': len(after), 'removed': 0, 'modified': 0,
                'affected_record_keys': list(sorted(after))[:60], 'truncated': len(after)>60}
    changed = sorted(set(before) ^ set(after) | {k for k in before.keys() & after.keys() if before[k] != after[k]})
    return {'added': len(after.keys() - before.keys()),
            'removed': len(before.keys() - after.keys()),
            'modified': sum(before[k] != after[k] for k in before.keys() & after.keys()),
            'affected_record_keys': changed[:60], 'truncated': len(changed)>60}


class SentinelWatch:
    def __init__(self, folder: Path, workspace: Path):
        self.folder = Path(folder)
        self.workspace = Path(workspace)
        self.lock = threading.RLock()
        self.path = self.workspace / 'sentinel-watch.json'
        self.events: list[dict] = []
        self.baseline: dict = {}
        self.current: dict | None = None
        self.error: str | None = None
        if self.path.exists():
            try:
                persisted = json.loads(self.path.read_text('utf-8'))
                if persisted.get('schema') != 1 or not isinstance(persisted.get('baseline'), dict):
                    raise WatchError('Invalid watcher state.')
                self.baseline = persisted['baseline']
                self.events = persisted.get('events', [])[-40:]
            except (OSError, ValueError, KeyError) as exc:
                raise WatchError('Saved Sentinel watcher state is invalid; refusing to trust it.') from exc
        self.scan()

    def _save(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as stream:
            json.dump({'schema': 1, 'baseline': self.baseline, 'events': self.events[-40:]}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.path)

    def scan(self) -> dict:
        with self.lock:
            try:
                observation = observe_pair(self.folder)
            except (WatchError, OSError) as exc:
                reason = str(exc)
                if self.error != reason:
                    self.events.append({'at': _utc(), 'kind': 'input_unavailable', 'message': reason})
                    self.events = self.events[-40:]
                    self._save()
                self.error = reason
                self.current = None
                return self.status()
            self.error = None
            if self.current is None or self.current['digest'] != observation['digest']:
                if self.baseline and observation['digest'] != self.baseline.get('digest'):
                    change = diff_rows(self.baseline, observation)
                    if not (self.events and self.events[-1].get('kind') == 'change_detected'
                            and self.events[-1].get('digest') == observation['digest']):
                        self.events.append({'at': _utc(), 'kind': 'change_detected',
                                            'digest': observation['digest'], 'delta': {k: v for k, v in change.items() if k != 'affected_record_keys'}})
                        self.events = self.events[-40:]
                        self._save()
            self.current = observation
            return self.status()

    def snapshot(self, digest: str) -> dict:
        with self.lock:
            self.scan()
            if not self.current or self.error:
                raise WatchError(self.error or 'No watched snapshot available.')
            if self.current['digest'] != digest:
                raise WatchError('Watched source changed since preview; scan again before promoting.')
            return deepcopy(self.current)

    def commit(self, snapshot: dict, run_id: str, policy: str, separation_policy: str):
        with self.lock:
            self.baseline = {'digest': snapshot['digest'], 'records': snapshot['records'],
                             'run_id': run_id, 'policy': policy, 'separation_policy': separation_policy}
            self.events.append({'at': _utc(), 'kind': 'snapshot_promoted', 'digest': snapshot['digest'],
                                'run_id': run_id})
            self.events = self.events[-40:]
            self.current = snapshot
            self._save()

    def status(self, active_run_id: str | None = None, affected_candidates: list[str] | None = None) -> dict:
        with self.lock:
            baseline = self.baseline
            pending = self.current is not None and self.current['digest'] != baseline.get('digest')
            change = diff_rows(baseline, self.current) if self.current else None
            bound = bool(active_run_id and baseline.get('run_id') == active_run_id)
            return {'state': 'unavailable' if self.error else 'pending' if pending else 'current',
                    'error': self.error, 'latest_digest': self.current['digest'] if self.current else None,
                    'baseline_digest': baseline.get('digest'), 'baseline_run_id': baseline.get('run_id'),
                    'bound_to_active_run': bound, 'stale_release': bool(bound and pending),
                    'delta': change if pending else None,
                    'affected_candidate_ids': (affected_candidates or [])[:60],
                    'affected_candidates_truncated': bool(affected_candidates and len(affected_candidates) > 60),
                    'events': self.events[-15:], 'watched_files': ['train.jsonl', 'eval.jsonl']}
