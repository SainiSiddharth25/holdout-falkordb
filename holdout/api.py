"""Local HTTP API and buildless review interface."""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
import time
from pathlib import Path
import threading
from uuid import uuid4
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import run_agent
from .checkpoints import CheckpointStore
from .graph import GraphError, RevisionConflict
from .ingest import ImportError as HoldoutImportError, prepare_rows
from .service import HoldoutService, PROJECT_ROOT
from .sentinel import SentinelWatch, WatchError, diff_rows


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    method: str = "auto"
    expected_revision: int | None = None


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    train: list[dict]
    evaluation: list[dict]
    policy: str = "equal"
    title: str = "Imported dataset release"
    separation_policy: str = "answer_leakage"


class OrgPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str

class OrgReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: str
    decision: str

class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int | None = None
    max_candidates: int | None = Field(default=None, ge=1, le=128, strict=True)
    refresh: bool = False


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "deterministic"


class WatchPromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_digest: str
    expected_run_id: str
    policy: str = "equal"
    separation_policy: str = "answer_leakage"


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conservative: bool = True
    expected_revision: int | None = None


def create_app(fixture_path: str | Path | None = None, runtime_dir: str | Path | None = None,
               service: HoldoutService | None = None) -> FastAPI:
    initial_service = service
    holder = {"service": service, "worker": None, "scanner": None}
    # Persistence is opt-in for the normal local application, not injected test
    # services or explicit one-shot fixture executions.
    checkpoint_store = CheckpointStore(PROJECT_ROOT / ".runtime" / "sessions") if (
        service is None and fixture_path is None and runtime_dir is None
    ) else None

    # Only normal local installations have continuous watching enabled. Injected
    # test services are isolated from the workspace and may opt into explicit tests.
    watch = SentinelWatch(PROJECT_ROOT / 'watch-inputs', checkpoint_store.workspace) if checkpoint_store else None
    stop_watch = threading.Event()

    def impact_report(s: HoldoutService) -> dict:
        if not watch:
            return {'state': 'disabled'}
        snap = watch.status(active_run_id=s.run_id)
        if not snap.get('delta') or not snap.get('bound_to_active_run'):
            return snap
        changed = set(snap['delta']['affected_record_keys'])
        if snap['delta'].get('truncated'):
            # Complete set is used for graph-impact calculations; UI is bounded.
            before = watch.baseline.get('records', {})
            after = watch.current.get('records', {}) if watch.current else {}
            changed = set(before) ^ set(after) | {k for k in before.keys() & after.keys() if before[k] != after[k]}
        originals = {r['id']: f"{r['split']}:{r.get('original_id', r['id'])}" for r in s.store.records()}
        affected = [c['id'] for c in s.store.candidates()
                    if originals.get(c['train_id']) in changed or originals.get(c['eval_id']) in changed]
        snap['affected_candidate_ids'] = affected[:60]
        snap['affected_candidates_truncated'] = len(affected) > 60
        snap['affected_candidate_count'] = len(affected)
        snap['release_requires_revalidation'] = bool(s.release and snap['stale_release'])
        return snap

    def watch_guard(run_id: str) -> None:
        if watch:
            watch.scan()  # Fail closed even if background polling has not run yet.
            snap = watch.status(active_run_id=run_id)
            if snap.get('stale_release') or (snap.get('baseline_run_id') == run_id and snap.get('state') == 'unavailable'):
                raise ValueError('Watched source changed or is missing. Promote a reviewed snapshot before further actions.')

    def bind_watch(s: HoldoutService) -> None:
        if watch:
            s.source_watch_guard = watch_guard

    def require_fresh(s: HoldoutService) -> None:
        if watch:
            watch_guard(s.run_id)

    def scanner():
        interval = max(5, min(3600, int(os.environ.get('HOLDOUT_WATCH_INTERVAL', '15'))))
        while not stop_watch.wait(interval):
            if watch:
                watch.scan()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if holder["service"] is None:
            if checkpoint_store:
                saved = checkpoint_store.current()
                if saved:
                    # A new embedded graph is built and evidence independently
                    # replayed; no reliance on an RDB autosave interval.
                    restored_dir = checkpoint_store.workspace / (
                        saved["run_id"] + "-resume-" + uuid4().hex[:10]
                    )
                    holder["service"] = HoldoutService(
                        saved["catalog_path"], restored_dir,
                        checkpoint_store=checkpoint_store,
                        resume_id=saved["run_id"],
                    )
                else:
                    holder["service"] = HoldoutService(checkpoint_store=checkpoint_store)
                    checkpoint_store.activate(holder["service"].run_id,
                                              holder["service"].catalog_path)
            else:
                holder["service"] = HoldoutService(fixture_path, runtime_dir)
        bind_watch(holder['service'])
        app.state.holdout = holder
        if watch:
            holder['scanner'] = threading.Thread(target=scanner, name='sentinel-watch', daemon=True)
            holder['scanner'].start()
        try:
            yield
        finally:
            stop_watch.set()
            if holder['scanner']:
                holder['scanner'].join(timeout=3)
            worker = holder["worker"]
            if worker and worker.is_alive():
                worker.join(timeout=35)
            if initial_service is None and not (worker and worker.is_alive()):
                holder["service"].close()

    app = FastAPI(title="HOLDOUT Sentinel", version="0.9.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def same_origin_actions(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Actions require the local review interface's origin."}, status_code=403)
        if request.url.path == "/api/import" and request.method == "POST":
            length = request.headers.get("content-length")
            if length is None or not length.isdigit() or int(length) > 8 * 1024 * 1024:
                return JSONResponse({"detail": "Import request must declare a size up to 8 MiB. For larger datasets use CLI prepare."}, status_code=413)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ValueError)
    async def invalid_input(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing_input(request: Request, exc: KeyError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(RevisionConflict)
    async def stale_input(request: Request, exc: RevisionConflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(GraphError)
    async def graph_error(request: Request, exc: GraphError):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    def current() -> HoldoutService:
        return holder["service"]

    def require_idle(s: HoldoutService) -> None:
        if s.agent["status"] == "running":
            raise HTTPException(409, "An investigator is running; wait for completion before changing the release.")

    @app.get("/api/state")
    def state():
        s = current()
        result = s.state()
        result['sentinel_watch'] = impact_report(s)
        if result['sentinel_watch'].get('stale_release') or (result['sentinel_watch'].get('baseline_run_id') == s.run_id and result['sentinel_watch'].get('state') == 'unavailable'):
            # A receipt stays valid for its historical snapshot, but must not
            # be presented as approval to publish the CURRENT watched inputs.
            gate = result.get('organization', {}).get('gate', {})
            gate['previous_status'] = gate.get('status')
            gate['status'] = 'stale_input'
            gate['publishable'] = False
            if result.get('release'):
                result['release']['stale_watched_source'] = True
        return result

    @app.get('/api/sentinel')
    def sentinel_status():
        return impact_report(current())

    @app.post('/api/sentinel/scan')
    def sentinel_scan():
        if not watch:
            raise ValueError('Watcher is unavailable in this service configuration.')
        watch.scan()
        return impact_report(current())

    @app.post('/api/sentinel/promote')
    def sentinel_promote(body: WatchPromotion):
        if not watch or not checkpoint_store:
            raise ValueError('Watcher promotion requires the persistent local service.')
        s = current()
        with s.lock:
            require_idle(s)
            if body.expected_run_id != s.run_id:
                raise RevisionConflict('Active release changed since watch preview.')
            from .ingest import POLICIES
            from .policies import SEPARATION_POLICIES
            if body.policy not in POLICIES or body.separation_policy not in SEPARATION_POLICIES:
                raise ValueError('Unknown cost or conflict policy.')
            snapshot = watch.snapshot(body.expected_digest)
            destination = s.runtime_dir.parent / 'imports' / uuid4().hex
            prepare_rows(snapshot['train'], snapshot['evaluation'], destination,
                         policy=body.policy, separation_policy=body.separation_policy,
                         title='Sentinel watched dataset release')
            new = HoldoutService(destination / 'dataset.json',
                                 runtime_dir=s.runtime_dir.parent / uuid4().hex,
                                 checkpoint_store=checkpoint_store)
            try:
                # The watched bytes must still match the preview before activation.
                if watch.snapshot(body.expected_digest)['hashes'] != snapshot['hashes']:
                    raise WatchError('Watched files changed while preparing a new release.')
                # Evidence is deliberately not copied into the new release. Every
                # new candidate must pass its own checks; old approvals stay historical.
                watch.commit(snapshot, new.run_id, body.policy, body.separation_policy)
                checkpoint_store.activate(new.run_id, new.catalog_path)
            except BaseException:
                new.close()
                raise
            bind_watch(new)
            holder['service'] = new
            s.close()
            result = new.state()
            result['sentinel_watch'] = impact_report(new)
            return result

    @app.post("/api/plan")
    def plan(body: PlanRequest):
        s = current()
        with s.lock:
            require_idle(s)
            s.plan_investigation(body.expected_revision, body.max_candidates, refresh=body.refresh)
            return s.state()

    @app.get("/api/plan/report")
    def plan_report():
        import json
        return Response(json.dumps(current().planning_report(), ensure_ascii=False, indent=2),
                        media_type="application/json", headers={"Content-Disposition": 'attachment; filename="planning-report.json"'})

    @app.post("/api/examples/decision-impact")
    def example():
        s = current()
        with s.lock:
            require_idle(s)
            # Fixed allowlisted local fixture; no user-supplied path or network.
            new = HoldoutService(PROJECT_ROOT / "fixtures" / "decision-impact" / "dataset.json",
                                 runtime_dir=s.runtime_dir.parent / uuid4().hex,
                                 checkpoint_store=checkpoint_store)
            if checkpoint_store:
                checkpoint_store.activate(new.run_id, new.catalog_path)
            bind_watch(new)
            holder["service"] = new
            s.close()
            return new.state()

    @app.get("/api/memory")
    def memory(candidate_id: str | None = None, kind: str | None = None,
               limit: int = 60, include_stale: bool = False):
        if not 1 <= limit <= 100:
            raise ValueError("Memory limit must be 1–100.")
        return current().recall_memories(candidate_id, kind, limit=limit, include_stale=include_stale)

    @app.get("/api/memory/suggestions/{candidate_id}")
    def memory_suggestions(candidate_id: str):
        return current().suggest_procedures(candidate_id)

    @app.get("/api/organization")
    def organization_context():
        return current().organization_context()

    @app.post("/api/organization/policy")
    def organization_policy(body: OrgPolicyRequest):
        s = current()
        with s.lock:
            require_idle(s)
            s.organization_policy(body.policy_id)
            return s.state()

    @app.post("/api/organization/review")
    def organization_review(body: OrgReviewRequest):
        s = current()
        with s.lock:
            require_idle(s)
            require_fresh(s)
            s.organization_review(body.actor_id, body.decision)
            return s.state()

    @app.get("/api/coordination")
    def coordination():
        return current().coordination_status()

    @app.get("/api/candidates/{candidate_id}")
    def detail(candidate_id: str):
        return current().candidate_detail(candidate_id)

    @app.post("/api/investigate")
    def investigate(body: InvestigationRequest):
        s = current()
        with s.lock:
            require_idle(s)
            require_fresh(s)
            s.investigate(body.candidate_id, body.method, body.expected_revision)
            return s.state()

    @app.post("/api/import")
    def import_dataset(body: ImportRequest):
        s = current()
        with s.lock:
            require_idle(s)
            # Import into a unique snapshot under the persisted runtime root. Do
            # not replace the active graph unless both preparation and native
            # FalkorDB initialization succeed. Import never calls the model.
            destination = s.runtime_dir.parent / "imports" / uuid4().hex
            catalog = prepare_rows(body.train, body.evaluation, destination, policy=body.policy,
                                   title=body.title, separation_policy=body.separation_policy)
            new = HoldoutService(destination / "dataset.json",
                                 runtime_dir=s.runtime_dir.parent / uuid4().hex,
                                 checkpoint_store=checkpoint_store)
            if checkpoint_store:
                checkpoint_store.activate(new.run_id, new.catalog_path)
            bind_watch(new)
            holder["service"] = new
            s.close()
            return {**new.state(), "import_summary": catalog["import_summary"]}

    def _agent_public_failure(exc: BaseException) -> str:
        """Unwrap MCP task-group failures without leaking provider response bodies."""
        from .agent import AgentBudgetExceeded
        def leaves(error):
            nested = getattr(error, "exceptions", ())
            if nested:
                for sub in nested:
                    yield from leaves(sub)
            else:
                yield error
        leaf = list(leaves(exc))
        # Whitelist only internally generated messages.
        for err in leaf:
            if isinstance(err, AgentBudgetExceeded):
                return str(err)[:500]
        for err in leaf:
            if isinstance(err, RuntimeError) and str(err).startswith(("Model endpoint", "The model response", "The investigator context")):
                return str(err)[:500]
        return f"Investigator failed ({type(leaf[0]).__name__ if leaf else type(exc).__name__}); inspect backend logs."

    @app.post("/api/run")
    def run(body: RunRequest):
        s = current()
        with s.lock:
            require_idle(s)
            require_fresh(s)
            if body.mode not in {"deterministic", "graph_guided", "coordinated", "llm"}:
                raise ValueError("Unknown investigator mode.")
            if body.mode == "llm" and not s.provider_available:
                raise ValueError("Configure the model and API key on the backend first.")
            s.agent.update(status="running", mode=body.mode, steps=0, message="Starting MCP investigator.")

            def worker():
                try:
                    run_agent(s, body.mode)
                except Exception as exc:
                    with s.lock:
                        # Never expose provider response bodies or credentials.
                        message = _agent_public_failure(exc)
                        s.agent.update(status="failed", message=message)
                        s._event("agent_stopped", message)

            holder["worker"] = threading.Thread(target=worker, name="holdout-investigator", daemon=True)
            holder["worker"].start()
            return s.state()

    @app.post("/api/release")
    def release(body: ReleaseRequest):
        s = current()
        with s.lock:
            require_idle(s)
            require_fresh(s)
            s.stage_release(body.conservative, body.expected_revision)
            return s.state()

    @app.post("/api/reset")
    def reset():
        s = current()
        with s.lock:
            require_idle(s)
            # A watched release must remain bound to its monitored source. To
            # switch versions the operator must explicitly promote or import.
            if watch and watch.baseline.get('run_id') == s.run_id:
                raise HTTPException(409, 'This run is bound to watched inputs. Promote the new snapshot instead of resetting.')
            # A reset always captures a new graph and run; previous artifacts
            # remain in the old session directory for local inspection.
            new = HoldoutService(s.catalog_path, runtime_dir=s.runtime_dir.parent / uuid4().hex,
                                 checkpoint_store=checkpoint_store)
            if checkpoint_store:
                checkpoint_store.activate(new.run_id, new.catalog_path)
            bind_watch(new)
            holder["service"] = new
            s.close()
            return new.state()

    @app.get("/api/artifacts/{name}")
    def artifact(name: str):
        require_fresh(current())
        return Response(current().artifact(name), media_type="application/json" if name.endswith(".json") else "application/x-ndjson",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    app.mount("/", StaticFiles(directory=PROJECT_ROOT / "dist", html=True), name="review")
    return app
