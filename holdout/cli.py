"""Source-checkout CLI for local execution and reproducible review captures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="holdout", description="HOLDOUT · evidence-guided draft dataset repair")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Import local datasets and discover candidate leakage (no model or database needed).")
    prepare.add_argument("--train", required=True, help="Training records: JSONL, JSON array, CSV, or Parquet.")
    prepare.add_argument("--eval", required=True, help="Evaluation records: JSONL, JSON array, CSV, or Parquet.")
    prepare.add_argument("--output", required=True, help="New output fixture directory; must not already exist.")
    prepare.add_argument("--policy", choices=("equal", "balanced", "eval_first", "train_first"), default="equal")
    prepare.add_argument("--title", default="Imported dataset release")
    prepare.add_argument("--max-candidates", type=int, default=25000)
    git_cmd = commands.add_parser('git-snapshot', help='Capture tracked Git Python files and bounded history into offline JSONL input.')
    git_cmd.add_argument('--repo', required=True, help='Existing local Git repository. No cloning or network requests.')
    git_cmd.add_argument('--output', required=True, help='New .jsonl output file (must not exist).')
    git_cmd.add_argument('--revision', default='HEAD', help='Commit/ref to capture. Defaults to HEAD.')
    git_cmd.add_argument('--pattern', default='*.py', help='Glob for tracked regular files. Defaults to *.py.')
    git_cmd.add_argument('--limit', type=int, default=250, help='Maximum matching files.')
    git_cmd.add_argument('--history-depth', type=int, default=4, help='Maximum same-path Git history versions (1-12).')
    git_cmd.add_argument('--repo-label', help='Stable public label to use in the evidence graph.')
    doctor = commands.add_parser("doctor", help="Probe the real FalkorDB engine and native max-flow.")
    doctor.add_argument("--install-native-module", action="store_true")
    demo = commands.add_parser("demo", help="Run the actual investigation and export a review capture.")
    demo.add_argument("--fixture")
    demo.add_argument("--runtime-dir")
    demo.add_argument("--output", default="dist/demo-run.json")
    demo.add_argument("--mode", choices=["deterministic", "graph_guided", "coordinated", "llm"], default="deterministic")
    serve = commands.add_parser("serve", help="Run the local backend and review UI.")
    serve.add_argument("--fixture")
    serve.add_argument("--runtime-dir")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    mcp = commands.add_parser("mcp", help="Run the domain MCP server over stdio.")
    mcp.add_argument("--fixture")
    mcp.add_argument("--runtime-dir")
    verify = commands.add_parser("verify", help="Verify an exported release without a database or model.")
    verify.add_argument("directory")
    verify.add_argument("--fixture", help="Also compare against independently retained source evidence.")
    verify_plan_cmd = commands.add_parser("verify-plan", help="Verify planner certificates and sensitivity arithmetic without a database.")
    verify_plan_cmd.add_argument("report")
    verify_plan_cmd.add_argument("--fixture", help="Also replay evidence against independently retained captured inputs.")
    plan = commands.add_parser("plan", help="Compute a bounded native counterfactual plan; no model calls or evidence changes.")
    plan.add_argument("--fixture")
    plan.add_argument("--runtime-dir")
    plan.add_argument("--max-candidates", type=int, default=32)
    plan.add_argument("--output", required=True, help="New JSON report path; existing files are not overwritten.")
    # Separate the conflict definition from retention-cost weights.
    prepare.add_argument("--separation-policy", choices=["answer_leakage", "exact_record"], default="answer_leakage")
    args = parser.parse_args()
    if args.command == 'git-snapshot':
        from .git_sources import snapshot_git
        result = snapshot_git(args.repo, args.output, revision=args.revision, pattern=args.pattern,
                              limit=args.limit, history_depth=args.history_depth, repo_label=args.repo_label)
        print(json.dumps(result, indent=2))
        return
    if args.command == "prepare":
        from .ingest import prepare_dataset
        result = prepare_dataset(args.train, args.eval, args.output, policy=args.policy,
                                 title=args.title, max_candidates=args.max_candidates, separation_policy=args.separation_policy)
        print(json.dumps({"fixture": str(Path(args.output).absolute() / "dataset.json"),
                          "summary": result["import_summary"]}, indent=2))
        return
    if args.command == "verify-plan":
        from .planning import verify_plan
        from .evidence import load_fixture
        report = json.loads(Path(args.report).read_text())
        print(json.dumps(verify_plan(report, load_fixture(args.fixture) if args.fixture else None), indent=2))
        return
    if args.command == "plan":
        from .service import HoldoutService
        path = Path(args.output).resolve()
        if path.exists():
            parser.error("Plan output already exists; choose a new path.")
        service = HoldoutService(args.fixture, args.runtime_dir)
        try:
            summary = service.plan_investigation(max_candidates=args.max_candidates)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x") as handle:
                json.dump(service.planning_report(), handle, indent=2)
            print(json.dumps(summary, indent=2))
        finally:
            service.close()
        return
    if args.command == "doctor":
        from .service import PROJECT_ROOT
        import subprocess
        command = [sys.executable, str(PROJECT_ROOT / "scripts" / "probe_engine.py")]
        if args.install_native_module:
            command.append("--install-native-module")
        raise SystemExit(subprocess.call(command))
    if args.command == "verify":
        from .evidence import load_fixture
        from .service import verify_release
        print(json.dumps(verify_release(args.directory, load_fixture(args.fixture) if args.fixture else None), indent=2))
        return
    if args.command == "serve":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(args.fixture, args.runtime_dir), host=args.host, port=args.port)
        return
    if args.command == "mcp":
        from .mcp_server import create_server
        from .service import HoldoutService
        service = HoldoutService(args.fixture, args.runtime_dir)
        try:
            create_server(service).run()
        finally:
            service.close()
        return
    if args.command == "demo":
        from .agent import run_agent
        from .service import ARTIFACT_NAMES, HoldoutService
        service = HoldoutService(args.fixture, args.runtime_dir)
        try:
            snapshots = []

            def capture():
                state = service.state(include_details=True)
                state["mode"] = "recorded"
                snapshots.append(state)

            capture()
            run_agent(service, args.mode, capture)
            bundle = {"recorded": True, "schema_version": 1,
                      "capture": {"investigator": args.mode, "transport": "MCP SDK memory streams", "native_engine": service.engine,
                                  "note": "Actual captured execution. Replay does not run FalkorDB or call a model."},
                      "snapshots": snapshots,
                      "artifacts": {name: service.artifact(name) for name in ARTIFACT_NAMES},
                      "verification": service.release}
            output = Path(args.output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + "\n")
            print(json.dumps({"capture": str(output), "release_directory": str(service.release_dir),
                              "snapshots": len(snapshots), "verification": service.release}, indent=2))
        finally:
            service.close()


if __name__ == "__main__":
    main()
