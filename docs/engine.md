# FalkorDB engine and verification

HOLDOUT uses FalkorDB for its authoritative records, provenance traversal, candidate decisions and native `algo.maxFlow` optimization. Python constructs the capacity network and checks a flow/cut certificate. NetworkX is an explicitly labeled test and benchmark reference; it is never an application fallback.

## Tested runtime

- Python 3.12.14 on Linux x86-64, glibc 2.39.
- `falkordblite==0.10.0` supplies Redis 8.6.2 and an embedded FalkorDB 4.18.3 module. That bundled module does **not** register `algo.maxFlow`.
- The official FalkorDB **v6.0.1** Linux x86-64 module was downloaded separately and successfully tested with the bundled Redis executable.
- Module URL: https://github.com/FalkorDB/FalkorDB/releases/download/v6.0.1/falkordb-x64.so
- Pinned SHA256, checked against the release checksum: `155b2fedd99422adfa7f2541cd0d9becabc05839cf5319ed8d604341cdae445d`.
- Required shared libraries on the tested host resolved through its installed C/C++ runtime, OpenSSL 3 and standard Linux runtime libraries.

## Setup and proof

From the project directory after installing the pinned Python dependencies:

```bash
.venv/bin/python scripts/probe_engine.py --install-native-module --report docs/engine-probe.json
.venv/bin/python -m pytest tests/test_optimizer.py -q
.venv/bin/python scripts/benchmark_solver.py --repeats 5
```

The setup downloads only the pinned official release, verifies its hash, installs it under `.runtime/falkordb-x64.so`, and grants the execute bit that Redis requires on modules. A matching existing file is reused. The module and `.venv` are runtime dependencies, not source deliverables. `engine-probe.json` reports the module version read from the running server and a real cost-3 weighted-cover smoke test.

For a supported external server, set `HOLDOUT_FALKORDB_URL` in the environment. Credentials belong in that environment variable, never in tracked files. An explicitly managed local module can be selected with `HOLDOUT_FALKORDB_MODULE`. On other platforms use a compatible external FalkorDB deployment. The setup helper is explicitly Linux x86-64 only.

The embedded server exposes a private Unix socket and no TCP port. Some sandboxed environments require execution permission for local socket creation. Each embedded runtime directory belongs to one application process; use the external server configuration for multiple application processes. In-process stores share the same managed runtime using reference counts. Closing the final store terminates only the server process started by HOLDOUT, which persists its RDB before exiting.

## Algorithm and certificate

The exact formulation permits two fixed-role pools, pairwise train/evaluation conflicts, nonnegative integer exclusion costs and protected records. Total cost must be less than `2**52 - 1`, so capacities remain exactly representable by the native numeric engine. Conflict and protected-record edges use `M = sum(costs) + 1`. A conflict between two protected records yields a directly checkable infeasibility obstruction.

FalkorDB returns positive-flow edges, not the residual cut. HOLDOUT retrieves **all original capacities**, restores omitted zero flows, checks integer values/capacity bounds/flow conservation, and traverses the reconstructed residual network to recover the source-side cut. It requires no remaining augmenting path, equality of cut/flow/exclusion cost, coverage of every active conflict, and preservation of protected records. The empty release uses an explicitly marked zero-flow/zero-cut certificate without invoking the algorithm.

`validate_solution(records, candidates, result, conservative=False)` independently reconstructs the expected network before checking the exported certificate. It rejects certificates with altered capacities, missing edges, invalid flows, or mismatched exclusions. It does not run a new flow solver or need a database connection.

Every flow/capability probe uses its own UUID graph and deletes that exact graph. Tests preserve a separate ordinary sentinel graph. No code runs `FLUSHDB`, broad graph cleanup, or external server shutdown. Graph revisions are checked before and after solving, and candidate updates support atomic expected-revision checks.

## Performance scope

`solver-benchmark.json` contains a bounded synthetic comparison with NetworkX. Native end-to-end measurements include graph reads, network writes, native max-flow, certificate checking and temporary-graph cleanup. The reference reads the same graph snapshot and constructs/solves an in-memory network. Server startup is excluded. These are different mechanisms with explicit overheads; they do not establish FalkorDB superiority over PostgreSQL or a production dataset workload.

## Primary references

- Native procedure contract: https://docs.falkordb.com/algorithms/maxflow
- Embedded package: https://github.com/FalkorDB/falkordblite
- Official release: https://github.com/FalkorDB/FalkorDB/releases/tag/v6.0.1
