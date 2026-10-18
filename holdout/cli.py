"""Source-checkout CLI for local execution and reproducible review captures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="holdout", description="HOLDOUT · evidence-guided draft dataset repair")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Probe the real FalkorDB engine and native max-flow.")
    doctor.add_argument("--install-native-module", action="store_true")
    demo = commands.add_parser("demo", help="Run the actual investigation and export a review capture.")
    demo.add_argument("--fixture")
    demo.add_argument("--runtime-dir")
    demo.add_argument("--output", default="dist/demo-run.json")
    demo.add_argument("--mode", choices=["deterministic", "llm"], default="deterministic")
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
    args = parser.parse_args()
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
