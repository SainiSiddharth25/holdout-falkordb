"""Crash-consistent local checkpoints for HOLDOUT Sentinel's single active release.

These files are *recovery hints*, not authoritative evidence: every restored
investigation is replayed through the existing deterministic evidence tools
against an unchanged, fingerprinted source snapshot before it is accepted.
No provider transcript, credentials, or unchecked LLM assertions are stored.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

SCHEMA = 1
_RUN_ID = re.compile(r"[0-9a-f]{12}")


class CheckpointError(ValueError):
    pass


def _canonical(document: dict) -> bytes:
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_json(path: Path, data: dict) -> None:
    """Durably replace a JSON file; never leave a partial successful commit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(_canonical(data))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise CheckpointError("Checkpoint is missing, linked or exceeds 4 MiB.")
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CheckpointError("Checkpoint cannot be decoded.") from error
    if not isinstance(data, dict):
        raise CheckpointError("Checkpoint must contain a JSON object.")
    return data


def evidence_signature(evidence: dict) -> str:
    """Digest only replayable evidence facts, not graph-revision telemetry.

    Provenance search includes revision numbers that legitimately differ after
    rebuilding a graph from the same immutable evidence. Those are separately
    protected by the catalog and file fingerprints in the checkpoint.
    """
    attempts = evidence.get("attempts", [])
    return hashlib.sha256(_canonical({"attempts": [
        {key: attempt.get(key) for key in ("method", "verdict", "evidence")}
        for attempt in attempts
    ]})).hexdigest()


class CheckpointStore:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()
        self.checkpoints = self.workspace / "checkpoints"
        self.active_path = self.workspace / "active-session.json"

    def _path(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise CheckpointError("Invalid recovery run ID.")
        return self.checkpoints / (run_id + ".json")

    def save(self, *, run_id: str, catalog_path: str | Path, catalog_digest: str,
             fingerprints: dict, candidates: list[dict], release_staged: bool) -> None:
        self._path(run_id)
        decisions = []
        for candidate in candidates:
            evidence = candidate.get("evidence")
            if evidence is None:
                continue
            attempts = evidence.get("attempts") if isinstance(evidence, dict) else None
            if not isinstance(attempts, list) or not attempts:
                raise CheckpointError("Cannot checkpoint an evidence verdict without attempts.")
            methods = [a.get("method") for a in attempts]
            if any(method not in {"record_identity", "exact", "normalized_ast", "provenance"} for method in methods):
                raise CheckpointError("Unknown checkpoint evidence method.")
            decisions.append({"id": candidate["id"], "methods": methods,
                              "verdict": candidate["status"],
                              "evidence_sha256": evidence_signature(evidence)})
        data = {"schema_version": SCHEMA, "run_id": run_id,
                "catalog_path": str(Path(catalog_path).resolve()),
                "catalog_digest": catalog_digest, "fingerprints": fingerprints,
                "decisions": decisions, "release_staged": bool(release_staged)}
        _atomic_json(self._path(run_id), data)

    def load(self, run_id: str) -> dict:
        data = _load(self._path(run_id))
        if data.get("schema_version") != SCHEMA or data.get("run_id") != run_id:
            raise CheckpointError("Unsupported checkpoint schema or run ID mismatch.")
        if not isinstance(data.get("decisions"), list) or not isinstance(data.get("fingerprints"), dict):
            raise CheckpointError("Checkpoint lacks evidence decisions or source fingerprints.")
        return data

    def activate(self, run_id: str, catalog_path: str | Path) -> None:
        self.load(run_id)  # Pointer never precedes a complete checkpoint.
        _atomic_json(self.active_path, {"schema_version": SCHEMA, "run_id": run_id,
                                        "catalog_path": str(Path(catalog_path).resolve())})

    def current(self) -> dict | None:
        if not self.active_path.exists():
            return None
        data = _load(self.active_path)
        if data.get("schema_version") != SCHEMA or not _RUN_ID.fullmatch(str(data.get("run_id", ""))):
            raise CheckpointError("Active release pointer is invalid.")
        saved = self.load(data["run_id"])
        if data.get("catalog_path") != saved.get("catalog_path"):
            raise CheckpointError("Active release pointer disagrees with checkpoint.")
        return saved
