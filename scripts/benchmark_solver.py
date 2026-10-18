#!/usr/bin/env python3
"""Bounded solver comparison, not a database-system superiority benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import random
import statistics
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from holdout.graph import GraphStore
from holdout.optimizer import reference_solve, solve, validate_solution


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "solver-benchmark.json")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        raise ValueError("repeats must be 1–20")
    report = {"kind": "solver-mechanism comparison", "seed": 818, "python": platform.python_version(), "repeats": args.repeats, "limitations": ["Synthetic sparse bipartite conflicts, not a measured customer workload.", "Single process and local Unix socket; server startup excluded.", "Native path includes graph snapshot, temporary flow graph writes, native solve, certificate validation and cleanup.", "Reference path includes graph snapshot, in-memory NetworkX graph construction and minimum cut; certificate validation is timed separately only for native.", "These measurements do not compare FalkorDB against PostgreSQL, nor establish database performance superiority."], "results": []}
    rng = random.Random(818)
    with tempfile.TemporaryDirectory(prefix="holdout-benchmark-") as work:
        manager = GraphStore(Path(work), graph_name="holdout_bench_manager_" + uuid4().hex)
        try:
            report["capabilities"] = manager.capabilities()
            for count in [40, 200, 800]:
                n = count // 2
                records = [{"id": f"{split}{i}", "split": split, "cost": rng.randrange(1, 50), "protected": False} for split in ["train", "eval"] for i in range(n)]
                pairs = sorted({(i, rng.randrange(n)) for i in range(n) for _ in range(4)})
                candidates = [{"id": str(i), "train_id": f"train{t}", "eval_id": f"eval{e}", "status": "confirmed"} for i, (t, e) in enumerate(pairs)]
                store = GraphStore(Path(work), graph_name="holdout_bench_" + uuid4().hex)
                try:
                    store.seed({"id": "benchmark", "records": records, "candidates": candidates})
                    native_warm = solve(store)
                    reference_warm = reference_solve(store.records(), store.candidates())
                    assert native_warm["cost"] == reference_warm["cost"]
                    timings = {"native_end_to_end_ms": [], "native_procedure_ms": [], "networkx_from_graph_snapshot_ms": []}
                    for _ in range(args.repeats):
                        start = time.perf_counter()
                        native = solve(store)
                        timings["native_end_to_end_ms"].append((time.perf_counter() - start) * 1000)
                        timings["native_procedure_ms"].append(native["timing_ms"]["native_maxflow"])
                        start = time.perf_counter()
                        reference = reference_solve(store.records(), store.candidates())
                        timings["networkx_from_graph_snapshot_ms"].append((time.perf_counter() - start) * 1000)
                        assert native["cost"] == reference["cost"]
                        assert validate_solution(records, candidates, native)["valid"]
                    report["results"].append({"records": count, "conflicts": len(candidates), "exclusion_cost": native_warm["cost"], "equal_optimum": True, "median_ms": {k: round(statistics.median(v), 3) for k, v in timings.items()}, "samples_ms": {k: [round(x, 3) for x in v] for k, v in timings.items()}})
                finally:
                    store.close(delete_graph=True)
        finally:
            manager.close(delete_graph=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "results": report["results"]}, indent=2))


if __name__ == "__main__":
    main()
