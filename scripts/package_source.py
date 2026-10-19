#!/usr/bin/env python3
"""Package portable project source, fixtures, proof and setup; omit runtime data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {"README.md", "LICENSE", "pyproject.toml", "requirements-lock.txt", "Dockerfile", "compose.yaml", ".gitignore", ".dockerignore", ".env.example"}
SOURCE_DIRS = {"holdout", "fixtures", "dist", "examples", "tests", "scripts", "docs", "watch-inputs"}


def main() -> None:
    files = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in ROOT.iterdir() if path.is_file() and path.name in TOP_FILES}
    for directory in sorted(SOURCE_DIRS):
        for path in (ROOT / directory).rglob("*"):
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.is_symlink() or "__pycache__" in relative.parts or path.suffix in {".pyc", ".zip"} or relative.parts[:2] == ("dist", "downloads"):
                continue
            files[relative.as_posix()] = path.read_bytes()
    manifest = {"format": 1, "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
                "excludes": ["local virtual environment", "runtime binaries and sessions", "credentials", "Git and hosting metadata"]}
    files["archive-manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output = ROOT / "dist" / "downloads" / "holdout-source.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            archive.writestr("holdout/" + name, data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Source archive failed its integrity check.")
        for name, digest in manifest["files"].items():
            if hashlib.sha256(archive.read("holdout/" + name)).hexdigest() != digest:
                raise RuntimeError("Source archive content does not match its manifest.")
    print(json.dumps({"archive": str(output), "files": len(files), "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2))


if __name__ == "__main__":
    main()
