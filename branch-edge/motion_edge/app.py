"""Branch box: the till API and till screen for the branch LAN, plus the background sync loop.

Run it with ``python -m motion_edge`` (HTTPS on the LAN, see ``__main__.py``) or, for development:

    MOTION_EDGE_CENTRAL_URL=https://<render-app>.onrender.com \
    MOTION_EDGE_TOKEN=edge_... MOTION_EDGE_DB=./data/edge.sqlite3 \
    uvicorn motion_edge.app:app --host 127.0.0.1 --port 8080
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import service, webapi
from .store import Store

logger = logging.getLogger("motion.edge")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)
    register_id: int = Field(gt=0)


class LineIn(BaseModel):
    product_id: int
    quantity: str
    discount_pct: str = "0"


class PaymentIn(BaseModel):
    method: str
    amount: str
    reference: str | None = Field(default=None, max_length=120)


class SaleIn(BaseModel):
    client_key: str = Field(min_length=32, max_length=40)
    lines: list[LineIn] = Field(min_length=1, max_length=200)
    payments: list[PaymentIn] = Field(max_length=6)


def create_app(store: Store | None = None, client: httpx.Client | None = None, start_loop: bool = True) -> FastAPI:
    store = store or Store(os.environ.get("MOTION_EDGE_DB", "./data/edge.sqlite3"))
    if client is None:
        central_url = httpx.URL(os.environ["MOTION_EDGE_CENTRAL_URL"])
        dev_loopback = (os.environ.get("MOTION_EDGE_ALLOW_INSECURE_CENTRAL") == "1"
                        and central_url.host in {"127.0.0.1", "localhost", "::1"})
        if (central_url.scheme != "https" and not dev_loopback) or not central_url.host:
            raise RuntimeError("The central edge URL must use HTTPS.")
        client = httpx.Client(base_url=central_url,
                              headers={"Authorization": f"Bearer {os.environ['MOTION_EDGE_TOKEN']}"})
    syncer = service.Syncer(store, client)
    app = FastAPI(title="Motion branch edge", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.syncer = store, syncer
    # Sales are forwarded as soon as they are recorded (the till wakes the loop); otherwise the box
    # checks in every few seconds, and retries an unreachable head office at most once a minute.
    interval = float(os.environ.get("MOTION_EDGE_SYNC_SECONDS", "5"))
    auth_workers = threading.BoundedSemaphore(3)
    app.state.auth_workers = auth_workers

    @app.middleware("http")
    async def protect_till(request: Request, call_next):
        loopback = {"127.0.0.1", "::1", "localhost"}
        if request.url.scheme != "https" and (
            not request.client or request.client.host not in loopback or request.url.hostname not in loopback
        ):
            return JSONResponse({"message": "Connect to the branch till using HTTPS."}, status_code=403)
        response = await call_next(request)
        if "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def loop():
        backoff = interval
        while True:
            r = syncer.sync_once(force_pull=False)
            backoff = interval if r["ok"] else min(backoff * 2, 60)
            syncer.wake.wait(timeout=backoff)
            syncer.wake.clear()

    if start_loop:
        @app.on_event("startup")
        def _start():
            threading.Thread(target=loop, daemon=True, name="edge-sync").start()

    @app.exception_handler(service.EdgeError)
    async def _err(request: Request, exc: service.EdgeError):
        return JSONResponse({"error": "edge", "message": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid", "message": "Some fields are missing or invalid."}, status_code=422)

    def tok(authorization: str | None) -> str | None:
        return authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None

    @app.get("/edge/status")
    def status():
        return service.status(store)

    @app.get("/edge/registers")
    def registers():
        return {"registers": store.get_meta("registers") or [], "branch": store.get_meta("branch"),
                "brand": store.get_meta("brand"), "test_data": store.get_meta("test_data")}

    @app.post("/edge/login")
    def login(body: LoginIn, request: Request):
        if not auth_workers.acquire(blocking=False):
            raise service.EdgeError("The till is busy signing in. Try again shortly.", 429)
        try:
            return service.login(store, body.username, body.password, body.register_id,
                                 client_ip=request.client.host if request.client else "unknown")
        finally:
            auth_workers.release()

    @app.get("/edge/catalog")
    def catalog(authorization: str | None = Header(default=None)):
        service.session(store, tok(authorization))
        with store.connect() as c:
            items = []
            reserved = service.reservations(c)
            for r in c.execute("SELECT id, data FROM items"):
                it = json.loads(r["data"])
                it["available"] = str(service.available(store, c, r["id"], reserved))
                items.append(it)
        return {"items": items, "policy": store.get_meta("policy"), "price_version": store.get_meta("price_version")}

    @app.post("/edge/sales")
    def sale(body: SaleIn, authorization: str | None = Header(default=None)):
        sess = service.session(store, tok(authorization))
        return service.sell(store, sess, client_key=body.client_key, lines=[x.model_dump() for x in body.lines],
                            payments=[x.model_dump() for x in body.payments])

    @app.get("/edge/sales")
    def sales(authorization: str | None = Header(default=None)):
        sess = service.session(store, tok(authorization))
        with store.connect() as c:
            rows = c.execute("""SELECT * FROM outbox WHERE (? OR cashier=?)
                                ORDER BY created_at DESC LIMIT 100""",
                             ("branch_manager" in json.loads(sess["roles"]), sess["username"])).fetchall()
        return {"sales": [service._receipt(r) | {"last_error": r["last_error"], "attempts": r["attempts"]} for r in rows]}

    @app.post("/edge/sync")
    def sync_now(authorization: str | None = Header(default=None)):
        sess = service.session(store, tok(authorization))
        if "branch_manager" not in json.loads(sess["roles"]):
            raise service.EdgeError("Branch manager permission is required to run sync.", 403)
        return syncer.sync_once()

    page = Path(__file__).with_name("till.html")

    # Basic fallback page (cash sales only), kept for troubleshooting at /basic.
    @app.get("/basic")
    def basic_till():
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    @app.get("/till.js")
    def till_script():
        return FileResponse(page.with_suffix(".js"), media_type="text/javascript")

    app.include_router(webapi.build_router(app.state))
    web_dir = Path(os.environ.get("MOTION_EDGE_WEB_DIR") or Path(__file__).with_name("web"))
    webapi.mount_web(app, web_dir)
    return app


def _default_app():
    if os.environ.get("MOTION_EDGE_CENTRAL_URL") and os.environ.get("MOTION_EDGE_TOKEN"):
        return create_app()
    return None


app = _default_app()
