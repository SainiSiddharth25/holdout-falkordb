"""Local HTTP API and buildless review interface."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import threading
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import run_agent
from .graph import GraphError, RevisionConflict
from .service import HoldoutService, PROJECT_ROOT


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    method: str = "auto"
    expected_revision: int | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "deterministic"


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conservative: bool = True
    expected_revision: int | None = None


def create_app(fixture_path: str | Path | None = None, runtime_dir: str | Path | None = None,
               service: HoldoutService | None = None) -> FastAPI:
    initial_service = service
    holder = {"service": service, "worker": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if holder["service"] is None:
            holder["service"] = HoldoutService(fixture_path, runtime_dir)
        app.state.holdout = holder
        try:
            yield
        finally:
            worker = holder["worker"]
            if worker and worker.is_alive():
                worker.join(timeout=35)
            if initial_service is None and not (worker and worker.is_alive()):
                holder["service"].close()

    app = FastAPI(title="HOLDOUT", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def same_origin_actions(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Actions require the local review interface's origin."}, status_code=403)
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
        return current().state()

    @app.get("/api/candidates/{candidate_id}")
    def detail(candidate_id: str):
        return current().candidate_detail(candidate_id)

    @app.post("/api/investigate")
    def investigate(body: InvestigationRequest):
        s = current()
        with s.lock:
            require_idle(s)
            s.investigate(body.candidate_id, body.method, body.expected_revision)
            return s.state()

    @app.post("/api/run")
    def run(body: RunRequest):
        s = current()
        with s.lock:
            require_idle(s)
            if body.mode not in {"deterministic", "llm"}:
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
                        message = str(exc)[:1500] if isinstance(exc, (ValueError, RuntimeError, GraphError)) else f"Investigator failed ({type(exc).__name__}); inspect the backend logs."
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
            s.stage_release(body.conservative, body.expected_revision)
            return s.state()

    @app.post("/api/reset")
    def reset():
        s = current()
        with s.lock:
            require_idle(s)
            # A reset always captures a new graph and run; previous artifacts
            # remain in the old session directory for local inspection.
            new = HoldoutService(fixture_path)
            holder["service"] = new
            s.close()
            return new.state()

    @app.get("/api/artifacts/{name}")
    def artifact(name: str):
        return Response(current().artifact(name), media_type="application/json" if name.endswith(".json") else "application/x-ndjson",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    app.mount("/", StaticFiles(directory=PROJECT_ROOT / "dist", html=True), name="review")
    return app
