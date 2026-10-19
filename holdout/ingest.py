"""HOLDOUT Sentinel Stage 1: offline, policy-bound source import and discovery.

Candidate hints and source references are *not* provenance proof.  No user code is
executed and no URL is fetched.  The service's evidence checks remain authoritative.
"""
from __future__ import annotations

import ast
import builtins
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
from uuid import uuid4

from .evidence import _normalized

MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_RECORDS_PER_SPLIT = 5000
MAX_CONTENT_BYTES = 256 * 1024
MAX_CANDIDATES = 25000
POLICIES = {
    'equal': {'train': 1, 'eval': 1},
    'balanced': {'train': 2, 'eval': 5},
    'eval_first': {'train': 1, 'eval': 10},
    'train_first': {'train': 8, 'eval': 3},
}
TOKEN_RE = re.compile(r'[a-zA-Z_][a-zA-Z_0-9]*|\d+|[^\s]')


class ImportError(ValueError):
    """Import is invalid or exceeds bounded-resource constraints."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n'


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _flag(value: Any, label: str) -> bool:
    if value is None or value == '':
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {'true', 'false'}:
        return value.strip().lower() == 'true'
    raise ImportError(f'{label} must be a boolean.')


def _rows(path: Path) -> list[dict]:
    if not path.is_file() or path.is_symlink():
        raise ImportError('Inputs must be existing regular local files.')
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ImportError('Input file exceeds 40 MiB.')
    if path.suffix.lower() == '.parquet':
        try:
            import pyarrow.parquet as pq
        except builtins.ImportError as exc:
            raise ImportError('Parquet support requires pyarrow. Install: pip install pyarrow==21.0.0') from exc
        try:
            parquet = pq.ParquetFile(str(path))
            # Preflight row-group metadata before loading any decompressed batches.
            if sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.metadata.num_row_groups)) > 80 * 1024 * 1024:
                raise ImportError('Parquet uncompressed row groups exceed 80 MiB.')
            if parquet.metadata.num_rows > MAX_RECORDS_PER_SPLIT:
                raise ImportError('Each split must contain 1 to 5000 records.')
            out = []
            for batch in parquet.iter_batches(batch_size=128):
                out.extend(batch.to_pylist())
                if len(out) > MAX_RECORDS_PER_SPLIT:
                    raise ImportError('Each split must contain 1 to 5000 records.')
        except ImportError:
            raise
        except Exception as exc:
            raise ImportError('Malformed or unsupported Parquet input.') from exc
    else:
        try:
            data = path.read_text(encoding='utf-8-sig')
        except (UnicodeError, OSError) as exc:
            raise ImportError('Input must be readable UTF-8 text.') from exc
        try:
            if path.suffix.lower() == '.jsonl':
                out = [json.loads(line) for line in data.splitlines() if line.strip()]
            elif path.suffix.lower() == '.csv':
                out = list(csv.DictReader(data.splitlines()))
            elif path.suffix.lower() == '.json':
                out = json.loads(data)
            else:
                raise ImportError('Supported formats: .jsonl, .json, .csv, .parquet.')
        except (json.JSONDecodeError, csv.Error) as exc:
            raise ImportError(f'Malformed input: {path.name}.') from exc
    if not isinstance(out, list) or not out or len(out) > MAX_RECORDS_PER_SPLIT:
        raise ImportError('Each split must contain 1 to 5000 records.')
    if any(not isinstance(x, dict) for x in out):
        raise ImportError('Every input row must be an object.')
    return out


def _symbols(row: dict, content: str) -> tuple[list[str], str, bool]:
    explicit = row.get('solution_symbols')
    if explicit not in (None, ''):
        if isinstance(explicit, str):
            try:
                explicit = json.loads(explicit) if explicit.strip().startswith('[') else [v.strip() for v in explicit.split(',') if v.strip()]
            except json.JSONDecodeError as exc:
                raise ImportError('Invalid solution_symbols list.') from exc
        if not isinstance(explicit, list) or not all(isinstance(x, str) and x.isidentifier() for x in explicit):
            raise ImportError('solution_symbols must contain valid names.')
        return explicit, 'solution' if explicit else 'explanation', False
    if row.get('content_role') in {'explanation', 'scaffold', 'question', 'prompt'}:
        return [], 'explanation', False
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, RecursionError):
        return [], 'explanation', False
    funcs = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(('test_', '_'))]
    # Only a standalone single-function sample is presumed solution-bearing.
    # Multi-function modules require explicit 'solution_symbols' declarations.
    # This is a documented heuristic and can still be wrong for scaffold code.
    unambiguous = len(funcs) == 1 and not any(isinstance(node, ast.ClassDef) for node in tree.body)
    return (funcs if unambiguous else []), ('solution' if unambiguous else 'explanation'), unambiguous


def _feature(content: str, symbols: list[str]) -> dict[str, set[str]]:
    exact = set()
    norm = set()
    try:
        tree = ast.parse(content)
        lines = content.splitlines(keepends=True)
        defs = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name in symbols:
            node = defs.get(name)
            if node is None:
                continue
            start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
            raw = ''.join(lines[start:node.end_lineno]).rstrip('\r\n')
            exact.add(_digest(raw.encode()))
            normalized = _normalized(node)
            if normalized:
                norm.add(_digest(normalized.encode()))
    except (SyntaxError, ValueError, RecursionError):
        pass
    tokens = TOKEN_RE.findall(content.casefold())[:3000]
    shingles = {'\x1f'.join(tokens[i:i+5]) for i in range(len(tokens) - 4)}
    return {'bytes': {_digest(content.encode())}, 'functions': exact, 'ast': norm, 'shingles': shingles}


def _source_ids(row: dict) -> list[str]:
    result = []
    for key in ('source_id', 'parent_id', 'derived_from'):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            result.append(v.strip()[:500])
    value = row.get('parent_ids')
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.lstrip().startswith('[') else [x.strip() for x in value.split(',') if x.strip()]
        except json.JSONDecodeError:
            value = []
    if isinstance(value, list):
        result.extend(str(x)[:500] for x in value if isinstance(x, (str, int)) and str(x).strip())
    return sorted(set(result))[:12]


def _git_oid(data: bytes, oid: str) -> bool:
    if not isinstance(oid, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid):
        return False
    payload = b'blob ' + str(len(data)).encode('ascii') + b'\x00' + data
    algorithm = hashlib.sha256 if len(oid) == 64 else hashlib.sha1
    return algorithm(payload).hexdigest() == oid


def _capture_git(row: dict, content: str, rid: str, root: Path, entities: dict, links: list) -> dict | None:
    """Check supplied Git blob identities, capture versions and link graph nodes.

    Git commit/path claims were checked by the snapshot command when produced,
    but independent downstream verification cannot validate membership in a
    Git commit without a preserved tree or trusted repository. Such claims are
    therefore metadata, never lineage evidence of answer preservation.
    """
    snap = row.get('git_snapshot')
    if snap is None:
        return None
    if not isinstance(snap, dict):
        raise ImportError('git_snapshot must be an object.')
    label, path, commit, blob, digest = (snap.get(k) for k in ('repo_label', 'path', 'commit', 'blob_oid', 'sha256'))
    if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', label):
        raise ImportError('Invalid Git snapshot repository label.')
    if not isinstance(path, str) or not path or len(path) > 500 or '\x00' in path:
        raise ImportError('Invalid Git snapshot file path.')
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ImportError('Invalid Git snapshot commit.')
    if not _git_oid(content.encode('utf-8'), blob) or _digest(content.encode()) != digest:
        raise ImportError('Current Git snapshot blob bytes/hash disagree.')
    history = snap.get('history', [])
    if not isinstance(history, list) or not (1 <= len(history) <= 12):
        raise ImportError('Git history must contain 1 to 12 versions.')
    if not isinstance(history[0], dict) or history[0].get('commit') != commit or history[0].get('blob_oid') != blob:
        raise ImportError('Git history must start with captured HEAD blob.')
    previous = rid
    for i, entry in enumerate(history):
        if not isinstance(entry, dict):
            raise ImportError('Malformed Git history entry.')
        c, oid, body, sha = (entry.get(k) for k in ('commit', 'blob_oid', 'content', 'sha256'))
        if not isinstance(c, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', c):
            raise ImportError('Invalid Git history commit.')
        if not isinstance(body, str) or len(body.encode('utf-8')) > MAX_CONTENT_BYTES:
            raise ImportError('Git history version exceeds content limit.')
        if not _git_oid(body.encode(), oid) or _digest(body.encode()) != sha:
            raise ImportError('Git historical blob hash disagreement.')
        if i == 0 and body != content:
            raise ImportError('HEAD Git history bytes differ from record.')
        key = _digest((label + '\0' + path + '\0' + c + '\0' + oid).encode())[:24]
        eid = 'git-file-' + key
        relative = f'sources/{eid}.txt'
        (root / 'sources').mkdir(exist_ok=True)
        (root / relative).write_bytes(body.encode('utf-8'))
        entities[eid] = {'id': eid, 'kind': 'captured_git_blob', 'path': relative,
                         'repo_label': label, 'git_path': path, 'git_commit': c,
                         'git_blob_oid': oid, 'content_sha256': sha,
                         'note': 'Captured Git blob identity. Version ancestry does NOT prove solution-preserving provenance.'}
        links.append({'source': previous, 'target': eid, 'relation': 'DERIVED_FROM',
                      'note': 'Read-only captured Git version; no verified solution-preservation receipt.'})
        previous = eid
    return {'repo_label': label, 'git_path': path, 'git_commit': commit,
            'git_blob_oid': blob, 'captured_history_versions': len(history),
            'git_history_status': 'blob_contents_checked_commit_tree_not_preserved'}


from .policies import SEPARATION_POLICIES, validate_separation_policy


def _prepare(train: list[dict], evaluation: list[dict], root: Path, policy: str, title: str, max_candidates: int = MAX_CANDIDATES, separation_policy: str = "answer_leakage") -> dict:
    validate_separation_policy(separation_policy)
    if policy not in POLICIES:
        raise ImportError(f'Unknown policy: {policy}. Options: {", ".join(POLICIES)}')
    if type(max_candidates) is not int or not 1 <= max_candidates <= MAX_CANDIDATES:
        raise ImportError('max_candidates must be between 1 and 25000.')
    (root / 'records').mkdir(parents=True, exist_ok=True)
    records = []
    features = {}
    shared = {}
    entities = {}
    links = []
    for split, rows in (('train', train), ('eval', evaluation)):
        seen_user_ids = set()
        for i, row in enumerate(rows, 1):
            raw_id = row.get('id')
            original_id = str(raw_id) if raw_id is not None else str(i)
            if original_id in seen_user_ids:
                raise ImportError(f'Duplicate {split} original ID: {original_id[:100]}')
            seen_user_ids.add(original_id)
            content = row.get('content', row.get('code', row.get('text')))
            if not isinstance(content, str) or not content.strip():
                raise ImportError(f'{split} row {i} requires nonempty content, code or text.')
            encoded = content.encode('utf-8')
            if len(encoded) > MAX_CONTENT_BYTES:
                raise ImportError(f'{split} row {i} exceeds the 256 KiB record limit.')
            symbols, role, inferred = _symbols(row, content)
            if len(symbols) > 64:
                raise ImportError('At most 64 declared answer symbols per record.')
            identifier = f'{"t" if split == "train" else "e"}{i:06d}'
            filename = f'records/{identifier}.txt'
            (root / filename).write_bytes(encoded)
            protected = _flag(row.get('protected'), f'{identifier}.protected')
            record = {'id': identifier, 'original_id': original_id[:500], 'split': split,
                      'title': str(row.get('title') or original_id)[:300],
                      'cost': POLICIES[policy][split], 'protected': protected,
                      'content_path': filename, 'content_role': role,
                      'solution_symbols': symbols, 'solution_symbols_inferred': inferred,
                      'source_id': str(row.get('source_id') or '')[:500]}
            git_info = _capture_git(row, content, identifier, root, entities, links)
            if git_info:
                record['git_provenance'] = git_info
            records.append(record)
            features[identifier] = _feature(content, symbols)
            refs = _source_ids(row)
            shared[identifier] = set(refs)
            for source in refs:
                sid = 'source-' + _digest(source.encode())[:24]
                entities[sid] = {'id': sid, 'kind': 'unverified_source_reference', 'source_reference': source,
                                 'note': 'Imported metadata; no captured transformation receipt.'}
                links.append({'source': identifier, 'target': sid, 'relation': 'DERIVED_FROM',
                              'note': 'Metadata-derived hint, not certified provenance.'})
    trains = [r for r in records if r['split'] == 'train']
    evals = [r for r in records if r['split'] == 'eval']
    train_index = defaultdict(set)
    shingle_index = defaultdict(set)
    for r in trains:
        f = features[r['id']]
        for key in ('bytes', 'functions', 'ast'):
            for token in f[key]:
                train_index[(key, token)].add(r['id'])
        for source in shared[r['id']]:
            train_index[('source', source)].add(r['id'])
        for token in f['shingles']:
            shingle_index[token].add(r['id'])
    candidates = []
    for e in evals:
        ef = features[e['id']]
        hits = defaultdict(set)
        for key in ('bytes', 'functions', 'ast'):
            for token in ef[key]:
                for tid in train_index.get((key, token), ()):
                    hits[tid].add(key)
        for source in shared[e['id']]:
            for tid in train_index.get(('source', source), ()):
                hits[tid].add('shared_source_reference')
        # Approximate overlap only produces *unresolved* hypotheses, never a
        # verdict. Ignore extremely common shingles and restrict comparisons to
        # the best supported blocks; no all-pairs n*m scan.
        overlaps = Counter()
        if len(ef['shingles']) >= 8:
            for shingle in ef['shingles']:
                owners = shingle_index.get(shingle, ())
                if len(owners) <= 40:
                    for tid in owners:
                        overlaps[tid] += 1
            for tid, intersection in overlaps.most_common(40):
                union = len(ef['shingles']) + len(features[tid]['shingles']) - intersection
                if intersection >= 8 and union and intersection / union >= 0.84:
                    hits[tid].add('token_shingle_similarity')
        for tid, reasons in sorted(hits.items()):
            if len(candidates) >= max_candidates:
                raise ImportError(f'Discovery exceeds the {max_candidates} candidate limit; narrow the import.')
            candidate_id = f'c{len(candidates)+1:06d}'
            candidates.append({'id': candidate_id, 'train_id': tid, 'eval_id': e['id'],
                'status': 'unresolved', 'basis': 'auto_discovered',
                'discovery_signals': sorted(reasons),
                'hint': 'Automatically retrieved by ' + ', '.join(sorted(reasons)) + '. Verify using evidence tools; metadata alone is not proof.'})
    candidate_signals = Counter(signal for c in candidates for signal in c['discovery_signals'])
    catalog = {
        'id': 'holdout-import-' + uuid4().hex[:16],
        'title': title[:200],
        'description': 'Imported draft release. Automatic candidate discovery covers exact bytes, Python functions, bounded AST matches, token shingles and shared source references; discovery is incomplete. Retention costs are policy-assigned, not measured model quality.',
        'policy': {'id': f'auto-{policy}-{separation_policy}-v2', 'cost_policy': policy, 'weights': POLICIES[policy],
          'separation_policy': separation_policy,
          'separation_rule': SEPARATION_POLICIES[separation_policy]['rule'],
          'cost_rule': 'Preselected integer exclusion weights; protected records cannot be excluded.',
          'conflict_rule': SEPARATION_POLICIES[separation_policy]['rule'],
          'unknown_rule': 'Conservative repair covers all unresolved candidate edges.',
          'lineage_rule': 'Metadata-derived source links are not certified evidence of preserved solutions.'},
        'import_summary': {'separation_policy': separation_policy, 'record_count': len(records), 'candidate_count': len(candidates),
                           'candidate_signals': dict(sorted(candidate_signals.items())),
                           'total_possible_cross_split_pairs': len(trains) * len(evals),
                           'source_reference_count': sum(e['kind'] == 'unverified_source_reference' for e in entities.values()), 'captured_git_versions': sum(e['kind'] == 'captured_git_blob' for e in entities.values()), 'cost_policy': policy,
                           'discovery': ['full_record_sha256', 'function_source_sha256', 'normalized_python_ast', 'token_shingle_similarity', 'shared_metadata_source_reference'],
                           'notes': 'Offline bounded discovery; Git blobs have checked identities, but history links do not prove solution preservation. No semantic equivalence or exhaustive pairwise search.'},
        'records': records, 'entities': list(entities.values()), 'links': links, 'candidates': candidates,
    }
    (root / 'dataset.json').write_text(_json(catalog), encoding='utf-8')
    return catalog


def prepare_dataset(train_path: str | Path, eval_path: str | Path, output_dir: str | Path,
                    policy: str = 'equal', title: str = 'Imported dataset release', max_candidates: int = MAX_CANDIDATES, separation_policy: str = "answer_leakage") -> dict:
    """Prepare a new immutable snapshot; refuse to overwrite any existing path."""
    dest = Path(output_dir).absolute()
    if dest.exists() or dest.is_symlink():
        raise ImportError('Output directory exists. Use a new path to preserve previous evidence.')
    train = _rows(Path(train_path))
    evaluation = _rows(Path(eval_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.holdout-import-', dir=dest.parent))
    try:
        catalog = _prepare(train, evaluation, tmp, policy, title, max_candidates=max_candidates, separation_policy=separation_policy)
        # Check all imported material against the existing evidence contract.
        from .evidence import load_fixture
        load_fixture(tmp)
        tmp.rename(dest)
        return catalog
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def prepare_rows(train: list[dict], evaluation: list[dict], output_dir: str | Path,
                 policy: str = 'equal', title: str = 'Imported dataset release', max_candidates: int = MAX_CANDIDATES, separation_policy: str = "answer_leakage") -> dict:
    """For the local UI: pre-parsed bounded records, same pipeline as CLI."""
    if not isinstance(train, list) or not isinstance(evaluation, list):
        raise ImportError('Both train and eval must be arrays.')
    if not train or not evaluation or len(train) > MAX_RECORDS_PER_SPLIT or len(evaluation) > MAX_RECORDS_PER_SPLIT:
        raise ImportError('Each split must have between 1 and 5000 records.')
    if any(not isinstance(r, dict) for r in train + evaluation):
        raise ImportError('Each record must be an object.')
    dest = Path(output_dir).absolute()
    if dest.exists():
        raise ImportError('Import destination already exists.')
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.holdout-import-', dir=dest.parent))
    try:
        catalog = _prepare(train, evaluation, tmp, policy, title, max_candidates=max_candidates, separation_policy=separation_policy)
        from .evidence import load_fixture
        load_fixture(tmp)
        tmp.rename(dest)
        return catalog
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
