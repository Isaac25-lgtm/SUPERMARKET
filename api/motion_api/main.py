"""FastAPI application (single Render web service: API + built React UI).

Startup binds the port immediately.  When MOTION_AUTO_BOOTSTRAP=1 (Render),
database bootstrap (migrations, Tryton module activation, base setup and,
if MOTION_SEED_DEMO=1, TEST DATA) runs in a background thread; until it
finishes every /api route answers 503 and /healthz reports progress.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import tryton as t
from .errors import AppError
from .settings import get_settings
from .web import MotionJSON, error_response

logging.basicConfig(level=os.environ.get("MOTION_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("motion")

STATE = {"ready": False, "phase": "starting", "error": None, "started": time.time()}


def _bootstrap_worker() -> None:
    from . import bootstrap

    try:
        STATE["phase"] = "bootstrapping database"
        bootstrap.main([])  # migrations + Tryton modules + base setup
        STATE["phase"] = "loading engine"
        t.init_pool()
        STATE.update(ready=True, phase="ready")
    except Exception as exc:  # noqa: BLE001 - surfaced via /healthz, details in logs
        logger.exception("bootstrap failed")
        STATE.update(phase="failed", error=type(exc).__name__)
        return
    if get_settings().seed_demo:
        # TEST DATA history is generated while the system is already usable; it is
        # resumable (per-day phases) if the container restarts mid-way.
        from . import seed

        for attempt in range(1, 4):  # resumable: completed phases are skipped on retry
            try:
                STATE["phase"] = "ready (seeding TEST DATA in background)"
                seed.run(get_settings())
                STATE.update(phase="ready", error=None)
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("TEST DATA seed attempt %d failed", attempt)
                STATE.update(phase="ready (TEST DATA seed incomplete — see logs)", error=type(exc).__name__)
                time.sleep(60 * attempt)


def create_app() -> FastAPI:
    settings = get_settings()
    t.configure(settings)
    app = FastAPI(title="Motion Supermarket Platform", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/openapi.json" if not settings.is_production else None)

    from .routes import core, operations, retail

    app.include_router(core.router)
    app.include_router(retail.router)
    app.include_router(operations.router)

    @app.on_event("startup")
    def _startup():
        if os.environ.get("MOTION_AUTO_BOOTSTRAP") == "1":
            threading.Thread(target=_bootstrap_worker, name="bootstrap", daemon=True).start()
        else:
            t.init_pool()
            STATE.update(ready=True, phase="ready")

    @app.middleware("http")
    async def _middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        if request.url.path.startswith("/api") and not STATE["ready"]:
            return MotionJSON({"error": "starting", "message": f"System is starting ({STATE['phase']}). Retry shortly."},
                              status_code=503, headers={"Retry-After": "10"})
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return error_response(exc)

    @app.exception_handler(t.EngineError)
    async def _engine_error(request: Request, exc: t.EngineError):
        return MotionJSON({"error": "business_rule", "message": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        issues = [{"field": ".".join(str(x) for x in e.get("loc", [])[1:]), "message": e.get("msg")} for e in exc.errors()]
        return MotionJSON({"error": "invalid", "message": "Some fields are invalid.", "details": {"issues": issues}},
                          status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return MotionJSON({"error": "server_error", "message": "Something went wrong. The error was logged.",
                           "request_id": getattr(request.state, "request_id", None)}, status_code=500)

    @app.get("/healthz")
    def healthz():
        status = 200 if STATE["ready"] else (500 if STATE["phase"] == "failed" else 200)
        return MotionJSON({"ready": STATE["ready"], "phase": STATE["phase"], "error": STATE["error"],
                           "version": __version__, "uptime_s": int(time.time() - STATE["started"])}, status_code=status)

    dist: Path = settings.web_dist
    if dist.is_dir():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return MotionJSON({"error": "not_found", "message": "Unknown API route."}, status_code=404)
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
    return app


app = create_app()
