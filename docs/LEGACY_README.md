# Historical setup and development notes

Historical versions use other ports; use the current root README for this archive.

# HOLDOUT Sentinel — Stage 1 real-source discovery preview

This post-submission build extends **Stage 0 durable checkpoint recovery** with a read-only local Git history snapshot command, content-bound Git blob provenance nodes, and bounded Parquet import. The existing JSONL/CSV importer, agent investigator, FalkorDB max-flow solver, and release verifier remain intact. **Imported metadata and Git history do not by themselves prove solution-preserving lineage.**

- **Live agent** is now the prominent first button; **Run deterministic policy** is the secondary action.
- **Run this preview separately** from the hackathon submission and Stage 0 checkout; Compose defaults to `http://127.0.0.1:8040`.
- [Stage 1 usage, examples and limits](docs/STAGE1_REAL_SOURCES.md)
- [Stage 1 validation and outstanding integration checks](docs/STAGE1_VALIDATION.md)
- [Stage 0 restart notes](docs/STAGE0_RESTART.md)

```bash
cp .env.example .env  # configure locally for a live model, leave blank for deterministic
# Never commit .env or expose model-provider credentials.
docker compose -p holdout-sentinel-s1 --env-file .env up -d --build
```

This new code has offline unit coverage for Git object capture, imported graph evidence, tamper resistance, and Parquet reader behavior, but **full native Docker import-to-release execution must still be tested in your WSL setup**. For large files use the CLI instead of the browser importer. The published demo output remains a prior recorded fixture, not a Stage 1 live-model benchmark.

---

# HOLDOUT Sentinel — Stage 0 preview

This **post-submission development preview** extends the working HOLDOUT v2 automatic-discovery importer with durable single-run recovery. It is the *foundation* of the proposed unified three-track Sentinel product, **not a claim that multi-agent coordination or the company brain is implemented**.

- [Sentinel staged roadmap and acceptance gates](docs/SENTINEL_ROADMAP.md)
- [Stage 0 restart/validation instructions](docs/STAGE0_RESTART.md)
- **New local port:** `8030` (run with `docker compose -p holdout-sentinel --env-file .env up -d --build`).
- Preserve the submitted hackathon release separately; do not overwrite your old repository or v2 checkout.
- Tested here without the native engine: checkpoint, importer and evidence unit tests. Full native restart test is **pending on your WSL/Docker environment**.

---

# HOLDOUT

**Trace dataset leakage. Keep the evidence. Repair the release.**

HOLDOUT turns uncertain overlap between a draft training pool and a draft internal evaluation pool into a precise graph problem: **find the least-cost records to exclude so that every active conflict is covered, while keeping required records.**

An investigator selects evidence tools through MCP. FalkorDB stores the records, provenance, candidate decisions and tool events, then runs native `algo.maxFlow` for an exact weighted bipartite vertex-cover reduction. HOLDOUT writes revised, self-contained training/evaluation manifests and checks a flow/cut certificate plus the original source evidence.

**Hackathon target: Track 1 — agents using graph queries, multihop evidence and graph algorithms to take a verifiable action.**

## HOLDOUT v2: real-data import (new)

**New in this build:** import your own authorized training/evaluation data instead of manually creating `dataset.json`, discover candidate conflicts from code and metadata, and assign additive exclusion costs using a preapproved policy. FalkorDB still stores the evidence graph and runs native maximum flow; no model output substitutes for verification. **The imported hypotheses are not automatically proven contamination.**

Start in a new directory to preserve the hackathon release:

```bash
cp .env.example .env       # optional model key, leave blank for deterministic mode
# Edit .env locally if using Requesty or another provider.
docker compose -p holdout-v2 --env-file .env up -d --build
```

Open **http://127.0.0.1:8020**. Import two JSONL files and choose `Equal`, `Balanced`, `Evaluation-first`, or `Training-first` before running the investigator. The local browser file import does not require a model key. For CSV or larger inputs, use the `prepare` CLI. Example datasets are in `examples/v2-import/`.

Detailed supported formats, discovery signals, provenance caveats, preservation policies, and resource limits: **[docs/V2_IMPORT.md](docs/V2_IMPORT.md)**.

**Working baseline caution:** this v2 archive was developed from the available 2026-10-18 source archive, not from a fresh clone of your latest GitHub commits. Review and merge changes rather than overwriting newer local work or the existing submission. Backend integration still requires the original pinned dependencies and compatible native FalkorDB module.

## What works

- Actual FalkorDB **6.0.1** native max-flow, with a capability probe that fails explicitly if the procedure is missing.
- Seven domain MCP tools; both the deterministic investigator and optional live model use actual MCP client/server sessions.
- Exact solution-function comparison, bounded identifier-normalized AST comparison, and graph-returned provenance supported by captured bytes and hash-bound transformation receipts.
- Separate confirmed-only lower bounds and conservative upper bounds. Unknown evidence remains unknown.
- Protected records, zero-cost records, infeasibility handling, stale-revision rejection and independent certificate checking.
- Verified `train.jsonl`, `eval.jsonl` and `repair.json` exports, including original content and SHA-256 hashes.
- A local interactive review interface and a hosted **recorded-run** review. Replay is explicitly labeled and does not pretend to call a model or database.

The bundled data is entirely self-authored synthetic evidence. No paid dataset, model key or Docker installation is needed for the tested Linux setup.

## Start locally

The tested setup is **Linux x86-64, Python 3.12, and an Ubuntu 24.04-compatible runtime**. On Windows, run these commands inside an appropriate WSL Linux distribution.

From the extracted `holdout` directory:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
python -m holdout doctor --install-native-module
python -m holdout serve
```

Open **http://127.0.0.1:8020** if using Compose (direct CLI serve defaults to port 8000). Click **Run deterministic policy** to execute a fresh investigation, inspect the conflict graph, and download the verified release.

The setup downloads a checksum-pinned official FalkorDB module. The installed `falkordblite` package supplies the Redis executable, but its bundled older module lacks the required algorithm. The engine starts on a private Unix socket with no TCP database listener. The current module installer supports Linux x86-64; other platforms can connect to a compatible external server using `HOLDOUT_FALKORDB_URL`.

If `python3.12 -m venv` is unavailable, install your distribution's Python 3.12 venv support first. The application requires ordinary permission to create a local Unix socket.

### Existing FalkorDB server

```bash
export HOLDOUT_FALKORDB_URL='redis://127.0.0.1:6379'
python -m holdout doctor
python -m holdout serve
```

The capability probe must report `native_maxflow: true`. HOLDOUT creates UUID-scoped release and temporary flow graphs; it never flushes a server or stops an external server. See [engine details](docs/engine.md).

### Docker alternative

```bash
docker compose up --build
```

Then open http://127.0.0.1:8000. The provided container recipe uses the same pinned module and Python dependencies. **Docker was unavailable in the development environment, so this recipe is supplied but was not executed here.** The Python/WSL path above was validated on Linux; WSL itself was not available for a separate platform test.

## Reproduce the recorded demonstration

```bash
python -m holdout demo --output dist/demo-run.json
python -m holdout verify examples/verified-release --fixture fixtures/demo/dataset.json
python -m pytest -q
```

`demo` runs the real native engine and MCP investigator, stages manifests, and captures the intermediate states. It prints the new release directory. The checked-in `examples/verified-release` is the verified release that accompanies the bundled capture.

`verify` needs neither a running database nor a model. With `--fixture`, it replays evidence assessments against the original captured artifacts as well as checking the mathematical certificate. Without that argument, it checks bundle consistency and optimality for the problem recorded in the receipt. Receipts use hashes, not digital signatures; retain the original fixture independently when evidence authenticity matters.

## The actual demo result

The fixture contains **8 training records, 6 evaluation records and 8 candidate conflicts**. Both pools have predeclared exclusion costs. Two records are required.

| Quantity | Before investigation | After investigation |
|---|---:|---:|
| Confirmed candidates | 0 | 5 |
| Rejected candidate claims | 0 | 1 |
| Unresolved candidates | 8 | 2 |
| Confirmed-only minimum cost | 0 | 10 |
| Conservative minimum cost | 27 | 22 |
| Records retained conservatively | 7 | 8 |

The final conservative repair excludes `t101`, `e202`, `t103`, `e206`, `t105` and `t107`. It retains eight records, including required records `t106` and `e203`. Resolving the shared-scaffold claim saves **5 predeclared cost units**. This measures this fixture's retention objective, not model accuracy, money, or general contamination-detection quality.

The two remaining unknowns concern missing source evidence and code with a meaningful operator difference. Their endpoints are covered in the conservative release. The cost-10 confirmed-only solution is a **lower bound**, not an export that can ignore those unknowns.

## Turn on a live model investigator

The live investigator supports **Responses** and **Chat Completions** tool interfaces. Current GPT-6 Astra and GPT-6.1 Sol tool calling requires Responses. For an OpenAI setup, create a local `.env` from `.env.example`, add your key, and use the included Responses configuration. See [the live-agent setup guide](docs/live-agent-setup.md).

```bash
export OPENAI_API_KEY='your-provider-key'
export HOLDOUT_MODEL='gpt-6.1-sol'
export HOLDOUT_API_MODE='responses'
export HOLDOUT_REASONING_EFFORT='low'
export HOLDOUT_MAX_OUTPUT_TOKENS=4096
export HOLDOUT_MAX_TOKENS=100000
python -m holdout serve
```

The UI then displays the separate live-model control. The model chooses among the same MCP tools; it cannot assign arbitrary evidence verdicts, execute supplied code, issue arbitrary Cypher, or choose output paths. Credentials stay on the backend. An existing server must be restarted after changing its environment. The Python CLI does not automatically load `.env`; explicitly load that trusted file with `set -a`, `source .env`, and `set +a` before starting it.

Responses mode sends `store: false` and carries forward response output items, including encrypted reasoning state and function-call IDs. Each function output goes back through the matching `call_id`. Truncated or failed responses stop before tool execution. For another provider with a Chat Completions-compatible tool endpoint, set `HOLDOUT_API_MODE=chat_completions`, set `HOLDOUT_API_BASE` to its base URL ending at `/v1`, and choose a model that supports function tools and `max_completion_tokens`. `HOLDOUT_REASONING_EFFORT` applies only to Responses mode. For backward compatibility, code launched without an explicit API mode uses Chat Completions.

Limits are configurable in `.env.example`: provider calls, tool calls, per-response output tokens, a conservative input/output token reservation, and elapsed time. The example allows 100,000 accounted tokens for a run; the code default without that setting remains 24,000. Elapsed time is a stopping budget; an in-flight native operation may complete. Provider usage is used when supplied and conservatively estimated otherwise. Provider-specific hidden billing is not a guaranteed hard cap.

**No real provider key was available during development.** Live tool-call orchestration, malformed/unknown tools and budget behavior are tested using a mocked HTTP provider over genuine MCP sessions. The included demo uses the deterministic policy. No claim of live-model effectiveness or quality is made.

## Connect another MCP client

From the project directory:

```bash
python -m holdout mcp
```

For a client configuration, set `command` to the absolute path of `.venv/bin/python`, `args` to `["-m", "holdout", "mcp"]`, and `cwd` to the absolute project directory. Diagnostics stay off the MCP stdout channel.

Tools:

| Tool | Purpose |
|---|---|
| `release_status` | Current evidence counts, lower/upper repairs and a heuristic next candidate |
| `list_candidates` | Candidate hypotheses, evidence and attempted methods |
| `inspect_record` | Bounded captured source and content hashes |
| `trace_provenance` | FalkorDB multihop paths and provenance metadata |
| `investigate_candidate` | Deterministically derive and record a bounded evidence verdict |
| `solve_release` | Exact native max-flow plus checked min-cut certificate |
| `stage_release` | Write and verify a conservative release at the current revision |

The MCP server can also run on loopback HTTP:

```bash
python -m holdout.mcp_server --transport streamable-http --port 8001
```

Each standalone server owns a run. The browser's built-in agents use MCP memory streams to the browser backend's same service, so they investigate the run visible in the UI. This is a protocol connection, not direct function-call simulation.

## Why the graph is central

The provenance graph answers which solution-bearing artifacts survived a chain of transformations. Captured evidence supports each hop; a shared ancestor name alone does not confirm leakage.

The conflict graph is a separate bipartite projection with fixed train/eval roles. Its weighted vertex-cover reduction gives an exact optimum under the declared constraints. The returned native flow is converted into a residual cut using **all original capacities**, including zero-flow edges omitted from the procedure's positive-flow output. A feasible flow and an equal-cost cut are the auditable optimality witness.

FalkorDB also stores candidate updates and tool events. The agent moves between graph exploration, evidence assessment, optimization and manifest creation. The value demonstrated here is a coherent, inspectable workflow and an exact reduction. See [architecture and proof](docs/architecture.md) and [representative Cypher](docs/queries.cypher).

## Baselines and performance

```bash
python scripts/benchmark_solver.py --repeats 5
python scripts/benchmark_investigation.py
```

The bounded solver comparison uses actual FalkorDB and an explicit NetworkX reference over the same graph snapshot. All measured optimum costs agree. On the recorded 40/200/800-record cases, the complete native path is **slower** than NetworkX, including temporary-network upload and certificate checking. Procedure-only time is reported separately. This does not demonstrate a database speedup, and the NetworkX baseline is never an application fallback.

The investigation ablation compares no investigation, exact checks, exact-plus-AST checks, and the full deterministic MCP tool policy against the same synthetic hypotheses and costs. It measures evidence resolution and retention value, not live LLM intelligence. See the machine-readable reports in `docs/` and [validation scope](docs/validation.md).

## Project map

```text
holdout/       Python service, evidence tools, native solver, agents, HTTP and MCP
fixtures/      Self-authored source captures, candidates and transformation evidence
dist/          Buildless review UI and actual recorded execution
examples/      Verified training/evaluation manifests and repair receipt
tests/         Evidence, optimizer, certificate, MCP, provider and API checks
scripts/       Native setup/probe and reproducible benchmarks
docs/          Problem statement, architecture, runtime and validation reports
```

## Scope and next evaluation

HOLDOUT currently accepts an explicit candidate catalog. Candidate discovery across a large corpus, approximate semantic similarity, multiple dataset partitions, quotas, group exclusions and non-additive objectives are outside this exact formulation. A normalized AST match is a bounded structural equality claim, not arbitrary semantic equivalence. Rejecting a scaffold-based candidate does not prove all possible independence.

Use this workflow before releasing draft training/evaluation pools. It does not undo training exposure, certify a public benchmark's integrity, or establish that no unobserved conflict exists. Broader split optimization such as [DataSAIL](https://www.nature.com/articles/s41467-025-58606-8) addresses a different, more general problem.

A next empirical milestone is a licensed external corpus with independently adjudicated candidate pairs, explicit false-negative measurement and a real-model tool-policy comparison. The project does not fabricate that evaluation.

MIT-licensed project source; see `LICENSE`. Third-party engines and packages retain their own licenses.
