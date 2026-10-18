"""FalkorDB is HOLDOUT's authoritative record, provenance and decision store.

An embedded runtime uses the Redis executable distributed by falkordblite and
an explicitly selected FalkorDB module. It opens a private Unix socket only.
Use HOLDOUT_FALKORDB_URL for an existing server; no server is flushed or stopped.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any
from uuid import uuid4

from falkordb import FalkorDB
from redis import Redis
from redis.exceptions import RedisError


class GraphError(RuntimeError):
    pass


class RevisionConflict(GraphError):
    pass


_RUNTIMES: dict[str, dict[str, Any]] = {}
_RUNTIME_LOCK = threading.RLock()
_RELATIONS = frozenset({"DERIVED_FROM", "CONTAINS", "USES_SCAFFOLD"})
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _embedded(runtime_dir: Path) -> tuple[FalkorDB, str]:
    """Acquire a process-scoped embedded runtime; server state survives close."""
    import redislite

    runtime_dir.mkdir(parents=True, exist_ok=True)
    key = str(runtime_dir.resolve())
    with _RUNTIME_LOCK:
        existing = _RUNTIMES.get(key)
        if existing:
            existing["refs"] += 1
            return FalkorDB(unix_socket_path=existing["socket"]), key
        override = os.environ.get("HOLDOUT_FALKORDB_MODULE")
        installed = PROJECT_ROOT / ".runtime" / "falkordb-x64.so"
        module = Path(override) if override else (installed if installed.exists() else Path(redislite.__falkordb_module__))
        if not module.is_file():
            raise GraphError("Configured FalkorDB module does not exist; run scripts/probe_engine.py --install-native-module.")
        socket_path = runtime_dir / "redis.sock"
        if len(os.fsencode(socket_path)) >= 100:
            socket_path = Path("/tmp") / ("holdout-" + hashlib.sha256(key.encode()).hexdigest()[:24] + ".sock")
        if socket_path.exists():
            raise GraphError("This embedded runtime is already active or has a stale socket. Use its existing service, another runtime directory, or an external FalkorDB URL.")
        log = (runtime_dir / "redis.log").open("ab")
        args = [
            str(redislite.__redis_executable__), "--port", "0", "--unixsocket", str(socket_path),
            "--unixsocketperm", "700", "--dir", str(runtime_dir.resolve()),
            "--dbfilename", "holdout.rdb", "--save", "60 1", "--appendonly", "no",
            "--loadmodule", str(module.resolve()), "--daemonize", "no",
        ]
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        probe = Redis(unix_socket_path=str(socket_path), socket_timeout=0.3, socket_connect_timeout=0.3)
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise GraphError("Embedded FalkorDB stopped during startup. Inspect this runtime's redis.log; no reference solver was substituted.")
                try:
                    if probe.ping():
                        break
                except (RedisError, OSError):
                    time.sleep(0.05)
            else:
                raise GraphError("Embedded FalkorDB startup timed out. A restricted environment may need permission for its local Unix socket.")
            db = FalkorDB(unix_socket_path=str(socket_path))
            _RUNTIMES[key] = {"refs": 1, "process": process, "socket": str(socket_path), "log": log}
            return db, key
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            log.close()
            raise
        finally:
            probe.close()


class GraphStore:
    def __init__(self, runtime_dir: Path, url: str | None = None, graph_name: str | None = None):
        self.runtime_dir = Path(runtime_dir).resolve()
        self.graph_name = graph_name or "holdout_release_" + uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", self.graph_name):
            raise ValueError("Graph name must be 1–120 letters, digits, underscores or hyphens.")
        self._runtime_key: str | None = None
        self._closed = False
        self._capabilities: dict | None = None
        selected_url = url or os.environ.get("HOLDOUT_FALKORDB_URL")
        if selected_url:
            self.db = FalkorDB.from_url(selected_url, socket_timeout=30, socket_connect_timeout=5)
            self.mode = "external"
        else:
            self.db, self._runtime_key = _embedded(self.runtime_dir)
            self.mode = "embedded"
        self.graph = self.db.select_graph(self.graph_name)
        self.graph.query("MERGE (m:ReleaseMeta {id:'release'}) ON CREATE SET m.revision=0")

    def _query(self, cypher: str, params: dict | None = None) -> list:
        if self._closed:
            raise GraphError("GraphStore is closed.")
        return self.graph.query(cypher, params or {}).result_set

    def revision(self) -> int:
        return int(self._query("MATCH (m:ReleaseMeta {id:'release'}) RETURN m.revision")[0][0])

    def seed(self, fixture: dict) -> None:
        if self.revision() != 0 or self.records():
            raise GraphError("A seeded graph cannot be overwritten. Create a new release graph.")
        records = fixture.get("records", [])
        candidates = fixture.get("candidates", [])
        by_id = {r["id"]: r for r in records}
        if len(by_id) != len(records):
            raise ValueError("Duplicate record IDs.")
        for r in records:
            if r.get("split") not in {"train", "eval"} or type(r.get("cost")) is not int or r["cost"] < 0:
                raise ValueError("Records require train/eval split and nonnegative integer cost.")
            if type(r.get("protected", False)) is not bool:
                raise ValueError("protected must be boolean.")
        if len({c["id"] for c in candidates}) != len(candidates):
            raise ValueError("Duplicate candidate IDs.")
        for c in candidates:
            if c.get("status") not in {"confirmed", "unresolved", "rejected"}:
                raise ValueError("Invalid candidate status.")
            if by_id.get(c.get("train_id"), {}).get("split") != "train" or by_id.get(c.get("eval_id"), {}).get("split") != "eval":
                raise ValueError("Candidates must connect a training record and an evaluation record.")
        entities = {e["id"]: dict(e) for e in fixture.get("entities", [])}
        for r in records:
            entities[r["id"]] = {**entities.get(r["id"], {}), **r}
        links = fixture.get("links", [])
        for link in links:
            if link["relation"] not in _RELATIONS or link["source"] not in entities or link["target"] not in entities:
                raise ValueError("Invalid provenance link or missing entity.")
        # All user strings are query parameters. Relation types come only from
        # the closed, validated allowlist above.
        if entities:
            self._query("UNWIND $rows AS row CREATE (n:Entity {id:row.id,data:row.data})", {"rows": [{"id": e["id"], "data": _json(e)} for e in entities.values()]})
        if records:
            self._query("UNWIND $rows AS row MATCH (r:Entity {id:row.id}) SET r:Record, r.split=row.split, r.cost=row.cost, r.protected=row.protected,r.record_data=row.data", {"rows": [{"id": r["id"], "split": r["split"], "cost": r["cost"], "protected": r.get("protected", False), "data": _json(r)} for r in records]})
        for relation in sorted(_RELATIONS):
            rows = [{"source": e["source"], "target": e["target"], "data": _json(e)} for e in links if e["relation"] == relation]
            if rows:
                self._query(f"UNWIND $rows AS row MATCH (s:Entity {{id:row.source}}),(t:Entity {{id:row.target}}) CREATE (s)-[:{relation} {{data:row.data}}]->(t)", {"rows": rows})
        if candidates:
            self._query("UNWIND $rows AS row MATCH (t:Record {id:row.train_id}),(e:Record {id:row.eval_id}) CREATE (c:Candidate {id:row.id,status:row.status,data:row.data}), (c)-[:TRAIN]->(t),(c)-[:EVAL]->(e)", {"rows": [{**c, "data": _json(c)} for c in candidates]})
        self._query("MATCH (m:ReleaseMeta {id:'release'}) SET m.revision=1,m.fixture_id=$id,m.title=$title", {"id": fixture.get("id", ""), "title": fixture.get("title", "")})

    def records(self) -> list[dict]:
        return [json.loads(row[0]) for row in self._query("MATCH (r:Record) RETURN r.record_data ORDER BY r.id")]

    def candidates(self) -> list[dict]:
        return [json.loads(row[0]) for row in self._query("MATCH (c:Candidate) RETURN c.data ORDER BY c.id")]

    def set_candidate(self, candidate_id: str, status: str, evidence: dict, expected_revision: int | None = None) -> int:
        if status not in {"confirmed", "rejected", "unresolved"}:
            raise ValueError("Invalid candidate status.")
        current = self._query("MATCH (c:Candidate {id:$id}) RETURN c.data", {"id": candidate_id})
        if not current:
            raise KeyError(candidate_id)
        candidate = json.loads(current[0][0])
        candidate.update(status=status, evidence=evidence)
        rows = self._query("MATCH (c:Candidate {id:$id}),(m:ReleaseMeta {id:'release'}) WHERE $expected < 0 OR m.revision=$expected SET c.status=$status,c.data=$data,m.revision=m.revision+1 RETURN m.revision", {"id": candidate_id, "status": status, "data": _json(candidate), "expected": -1 if expected_revision is None else expected_revision})
        if not rows:
            raise RevisionConflict("Evidence changed; reload and investigate the current revision.")
        return int(rows[0][0])

    def provenance(self, record_id: str, max_depth: int = 8) -> dict:
        if type(max_depth) is not int or not 0 <= max_depth <= 16:
            raise ValueError("max_depth must be between 0 and 16.")
        rows = self._query(f"MATCH p=(r:Entity {{id:$id}})-[:DERIVED_FROM|CONTAINS|USES_SCAFFOLD*0..{max_depth}]->(n) RETURN [x IN nodes(p) | x.data],[x IN relationships(p) | x.data] LIMIT 5001", {"id": record_id})
        if len(rows) > 5000:
            raise GraphError("Provenance exceeds the 5,000-path limit; narrow the investigation.")
        nodes: dict[str, dict] = {}
        links: dict[str, dict] = {}
        paths: list[dict] = []
        for raw_nodes, raw_links in rows:
            ns = [json.loads(x) for x in raw_nodes]
            ls = [json.loads(x) for x in raw_links]
            nodes.update({n["id"]: n for n in ns})
            links.update({_json(e): e for e in ls})
            paths.append({"nodes": [n["id"] for n in ns], "relations": [e["relation"] for e in ls]})
        return {"nodes": list(nodes.values()), "links": list(links.values()), "paths": paths, "revision": self.revision()}

    def add_event(self, event: dict) -> None:
        value = {"id": uuid4().hex, "recorded_at_ns": time.time_ns(), **event}
        self._query("CREATE (e:Event {id:$id,at:$at,data:$data})", {"id": value["id"], "at": value["recorded_at_ns"], "data": _json(value)})

    def events(self) -> list[dict]:
        return [json.loads(row[0]) for row in self._query("MATCH (e:Event) RETURN e.data ORDER BY e.at,e.id")]

    def capabilities(self) -> dict:
        if self._capabilities is not None:
            return dict(self._capabilities)
        modules = self.db.execute_command("MODULE", "LIST")
        modules = [m if isinstance(m, dict) else dict(zip(m[::2], m[1::2])) for m in modules]
        version = next((int(m.get("ver", 0)) for m in modules if m.get("name") == "graph"), 0)
        # Verify the running procedure, not a marketing version string.
        probe = self.db.select_graph("holdout_capability_" + uuid4().hex)
        native = False
        reason = None
        try:
            probe.query("CREATE (s:Flow {key:'source'}),(t:Flow {key:'sink'}),(s)-[:CAPACITY {capacity:3}]->(t)")
            rows = probe.query("MATCH (s:Flow {key:'source'}),(t:Flow {key:'sink'}) CALL algo.maxFlow({sourceNodes:[s],targetNodes:[t],relationshipTypes:['CAPACITY'],capacityProperty:'capacity'}) YIELD maxFlow RETURN maxFlow").result_set
            native = bool(rows and rows[0][0] == 3)
            if not native:
                reason = "Native max-flow smoke test returned an unexpected value."
        except RedisError:
            reason = "The running module does not provide a working algo.maxFlow procedure."
        finally:
            probe.delete()
        self._capabilities = {"database": "FalkorDB", "module_version": version, "module_version_text": f"{version // 10000}.{(version // 100) % 100}.{version % 100}", "native_maxflow": native, "native_procedure": "algo.maxFlow" if native else None, "runtime": self.mode, "reason": reason, "capability_revision": 1}
        return dict(self._capabilities)

    def close(self, delete_graph: bool = False) -> None:
        if self._closed:
            return
        try:
            if delete_graph:
                self.graph.delete()
        finally:
            self._closed = True
            if self._runtime_key:
                with _RUNTIME_LOCK:
                    entry = _RUNTIMES[self._runtime_key]
                    entry["refs"] -= 1
                    if entry["refs"] == 0:
                        # Only the process we started, never an external server.
                        entry["process"].terminate()
                        try:
                            entry["process"].wait(timeout=6)
                        except subprocess.TimeoutExpired:
                            entry["process"].kill()
                            entry["process"].wait(timeout=3)
                        entry["log"].close()
                        del _RUNTIMES[self._runtime_key]
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
