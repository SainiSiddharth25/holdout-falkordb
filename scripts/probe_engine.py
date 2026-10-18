#!/usr/bin/env python3
"""Install the checksum-pinned official Linux x86-64 module, then test it.

The wheel currently embeds FalkorDB 4.18.3, whose algo.maxFlow is unavailable.
This script never replaces that algorithm with a local reference solver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RELEASE = "v6.0.1"
MODULE_URL = "https://github.com/FalkorDB/FalkorDB/releases/download/v6.0.1/falkordb-x64.so"
MODULE_SHA256 = "155b2fedd99422adfa7f2541cd0d9becabc05839cf5319ed8d604341cdae445d"


def install_module() -> dict:
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("This pinned module is Linux x86-64 only. Configure HOLDOUT_FALKORDB_URL with a server supporting algo.maxFlow on other platforms.")
    target = ROOT / ".runtime" / "falkordb-x64.so"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == MODULE_SHA256:
        target.chmod(0o755)
    else:
        temporary = target.with_suffix(".download")
        try:
            with urlopen(MODULE_URL, timeout=60) as remote, temporary.open("wb") as output:
                digest = hashlib.sha256()
                while block := remote.read(1024 * 1024):
                    digest.update(block)
                    output.write(block)
            if digest.hexdigest() != MODULE_SHA256:
                raise RuntimeError("Official module checksum mismatch; refusing installation.")
            temporary.chmod(0o755)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    receipt = {"release": RELEASE, "url": MODULE_URL, "sha256": MODULE_SHA256, "source": "official FalkorDB GitHub release", "platform": "linux-x86_64"}
    (target.parent / "engine-release.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-native-module", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = {}
    if args.install_native_module:
        report["installation"] = install_module()
    from holdout.graph import GraphStore
    from holdout.optimizer import solve, validate_solution

    fixture = {
        "id": "native-smoke", "title": "Weighted cover smoke",
        "records": [{"id": rid, "split": side, "cost": cost, "protected": False} for rid, side, cost in [("A", "train", 2), ("B", "train", 10), ("X", "eval", 10), ("Y", "eval", 1)]],
        "candidates": [{"id": a + b, "train_id": a, "eval_id": b, "status": "confirmed"} for a, b in [("A", "X"), ("A", "Y"), ("B", "Y")]],
    }
    with tempfile.TemporaryDirectory(prefix="holdout-probe-") as work:
        store = GraphStore(Path(work))
        try:
            report["capabilities"] = store.capabilities()
            if report["capabilities"]["native_maxflow"]:
                store.seed(fixture)
                result = solve(store)
                report["smoke"] = {"expected_cost": 3, "actual_cost": result["cost"], "excluded": result["excluded"], "verification": validate_solution(fixture["records"], fixture["candidates"], result)}
                if result["cost"] != 3:
                    raise RuntimeError("Native smoke cost is incorrect.")
            else:
                report["next_step"] = "Run python scripts/probe_engine.py --install-native-module, or configure HOLDOUT_FALKORDB_URL with a compatible external server."
        finally:
            store.close(delete_graph=True)
    text = json.dumps(report, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n")
    return 0 if report["capabilities"]["native_maxflow"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
