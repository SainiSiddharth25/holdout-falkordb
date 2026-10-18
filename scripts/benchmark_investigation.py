#!/usr/bin/env python3
"""Reproducible evidence-tool ablation on HOLDOUT's captured synthetic fixture.

All strategies start from identical hypotheses and costs. All investigations
use real MCP and the same production service; all repair bounds use native
FalkorDB max-flow. This does not evaluate an LLM or general leakage detection.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from holdout.agent import deterministic
from holdout.mcp_server import local_client, result_dict
from holdout.service import HoldoutService


async def run_partial(service: HoldoutService, methods: list[str]) -> int:
    """Each method visits every hypothesis still unresolved after earlier tools."""
    count = 0
    async with local_client(service) as client:
        for method in methods:
            listing = result_dict(await client.call_tool("list_candidates", {}))
            for candidate in listing["candidates"]:
                if candidate["status"] == "unresolved":
                    result_dict(await client.call_tool("investigate_candidate", {"candidate_id": candidate["id"], "method": method}))
                    count += 1
        result_dict(await client.call_tool("stage_release", {}))
    return count


def blanket(records: list[dict], candidates: list[dict], split: str) -> dict:
    """An explicitly narrow conservative baseline; protected records are binding."""
    by_id = {record["id"]: record for record in records}
    key = "train_id" if split == "train" else "eval_id"
    active = [c for c in candidates if c["status"] in {"confirmed", "unresolved"}]
    selected = {candidate[key] for candidate in active}
    protected = sorted(rid for rid in selected if by_id[rid].get("protected"))
    return {
        "policy": f"Exclude every {split} endpoint of an active conservative conflict.",
        "feasible": not protected,
        "cost": sum(by_id[rid]["cost"] for rid in selected) if not protected else None,
        "excluded": sorted(selected) if not protected else [],
        "protected_obstruction": protected,
        "note": "An infeasible blanket policy has no comparable valid cost; it is not counted as a repair.",
    }


async def run_strategy(name: str, work: Path, fixture_path: str | None) -> dict:
    service = HoldoutService(fixture_path=fixture_path, runtime_dir=work)
    try:
        started = time.perf_counter()
        if name == "no_investigation":
            calls = await run_partial(service, [])
        elif name == "exact_only":
            calls = await run_partial(service, ["exact"])
        elif name == "exact_then_ast":
            calls = await run_partial(service, ["exact", "normalized_ast"])
        elif name == "full_deterministic_mcp":
            await deterministic(service)
            calls = service.agent["steps"]
        else:
            raise ValueError(name)
        elapsed = time.perf_counter() - started
        bounds = service.bounds()
        candidates = service.store.candidates()
        records = service.store.records()
        events = service.store.events()
        inspections = [event for event in events if event.get("tool") == "investigate_candidate"]
        methods = Counter(event["result"]["method"] for event in inspections)
        counts = Counter(candidate["status"] for candidate in candidates)
        upper, lower = bounds["upper"], bounds["lower"]
        assert upper["engine"] == lower["engine"] == "falkordb-native-maxflow"
        assert service.release and service.release["verified"]
        return {
            "strategy": name,
            "fixture_catalog_sha256": service.catalog_sha256,
            "input_records": len(records), "input_candidates": len(candidates),
            "module_version": service.engine["module_version_text"],
            "solver": upper["engine"],
            "lower_cost": lower["cost"], "upper_cost": upper["cost"],
            "gap": bounds["gap"],
            "counts": {status: counts[status] for status in ["confirmed", "rejected", "unresolved"]},
            "kept_count": len(upper["kept"]),
            "kept_by_split": {split: sum(r["id"] in upper["kept"] for r in records if r["split"] == split) for split in ["train", "eval"]},
            "excluded": upper["excluded"],
            "candidate_investigation_calls": calls,
            "inspection_events": len(inspections),
            "inspection_events_by_method": dict(sorted(methods.items())),
            "elapsed_seconds": round(elapsed, 6),
            "release_verified": service.release["verified"],
            "candidate_verdicts": [{"id": candidate["id"], "status": candidate["status"], "final_method": candidate.get("evidence", {}).get("method")} for candidate in candidates],
            "blanket_policies": {split: blanket(records, candidates, split) for split in ["train", "eval"]},
        }
    finally:
        # HoldoutService retains ordinary interactive runs. Benchmark ownership
        # permits deletion of exactly its freshly generated graph, then closure.
        service.store.close(delete_graph=True)


async def benchmark(fixture_path: str | None) -> dict:
    rows = []
    with tempfile.TemporaryDirectory(prefix="holdout-investigation-benchmark-") as temporary:
        root = Path(temporary)
        for strategy in ["no_investigation", "exact_only", "exact_then_ast", "full_deterministic_mcp"]:
            rows.append(await run_strategy(strategy, root / strategy, fixture_path))
    assert len({row["fixture_catalog_sha256"] for row in rows}) == 1
    assert len({(row["input_records"], row["input_candidates"]) for row in rows}) == 1
    if fixture_path is None:
        # These are regression checks on this captured example, not externally
        # supplied truth labels available to investigation tools.
        assert rows[0]["upper_cost"] == 27
        assert rows[-1]["upper_cost"] == 22 and rows[-1]["lower_cost"] == 10
        assert rows[-1]["counts"] == {"confirmed": 5, "rejected": 1, "unresolved": 2}
    return {
        "kind": "synthetic captured-fixture evidence-tool ablation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture": str(Path(fixture_path).resolve()) if fixture_path else "fixtures/demo/dataset.json",
        "fixed_controls": ["Identical initial candidate graph, record content and predeclared costs.", "Identical protected-record policy and conservative release requirement.", "Native FalkorDB algo.maxFlow for both lower and upper bounds.", "Same evidence assessors and revision-bound production service.", "Each strategy starts in its own runtime with a fresh UUID release graph.", "All investigation and staging actions use real MCP; no model provider is called."],
        "metric_definitions": {
            "lower_cost": "Optimal exclusion cost over confirmed conflicts only.",
            "upper_cost": "Optimal exclusion cost over confirmed plus unresolved conflicts; used for release export.",
            "kept_count": "Number of records retained by the conservative optimal repair.",
            "candidate_investigation_calls": "Actual calls to investigate_candidate; auto can invoke several evidence methods.",
            "inspection_events": "Count of emitted investigate_candidate audit events, one per actual evidence-method attempt.",
            "elapsed_seconds": "Single-run strategy and staging time after native service startup, reported descriptively rather than as a performance claim.",
        },
        "limitations": ["This is a deliberately constructed synthetic fixture with eight captured hypotheses, not an estimate of field detection precision or recall.", "The full run uses a fixed deterministic investigation policy; no LLM capability or adaptive model benefit is measured.", "Exact and AST mismatches remain unresolved rather than being treated as clean.", "Confirmed-only lower bounds can increase when new conflicts are established; conservative costs decrease only when supported rejections remove candidate constraints.", "The known versus conservative gap may remain because unresolved relationships are explicitly retained.", "A lower exclusion cost is measured against fixed declared costs, not against model evaluation scores or independently demonstrated data value.", "The ablation measures the contribution of the available evidence methods on this fixture; it does not establish FalkorDB performance superiority or complete contamination detection."],
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "investigation-benchmark.json")
    args = parser.parse_args()
    report = asyncio.run(benchmark(args.fixture))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "results": [{key: row[key] for key in ["strategy", "lower_cost", "upper_cost", "counts", "kept_count", "candidate_investigation_calls", "inspection_events", "inspection_events_by_method"]} for row in report["results"]]}, indent=2))


if __name__ == "__main__":
    main()
