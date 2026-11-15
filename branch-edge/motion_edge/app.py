"""Branch edge HTTP API + background sync loop.

    MOTION_EDGE_CENTRAL_URL=https://<render-app>.onrender.com \
    MOTION_EDGE_TOKEN=edge_... MOTION_EDGE_DB=./data/edge.sqlite3 \
    uvicorn motion_edge.app:app --host 0.0.0.0 --port 8080

Bind to the branch LAN only (firewall the port from the internet).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import service
from .store import Store

logger = logging.getLogger("motion.edge")


class LoginIn(BaseModel):
    username: str
    password: str
    register_id: int


class LineIn(BaseModel):
    product_id: int
    quantity: str
    discount_pct: str = "0"


class PaymentIn(BaseModel):
    method: str
    amount: str
    reference: str | None = None


class SaleIn(BaseModel):
    client_key: str = Field(min_length=32, max_length=40)
    lines: list[LineIn] = Field(min_length=1, max_length=200)
    payments: list[PaymentIn] = Field(min_length=1, max_length=6)


def create_app(store: Store | None = None, client: httpx.Client | None = None, start_loop: bool = True) -> FastAPI:
    store = store or Store(os.environ.get("MOTION_EDGE_DB", "./data/edge.sqlite3"))
    if client is None:
        client = httpx.Client(base_url=os.environ["MOTION_EDGE_CENTRAL_URL"],
                              headers={"Authorization": f"Bearer {os.environ['MOTION_EDGE_TOKEN']}"})
    syncer = service.Syncer(store, client)
    app = FastAPI(title="Motion branch edge", docs_url=None, redoc_url=None)
    app.state.store, app.state.syncer = store, syncer
    interval = float(os.environ.get("MOTION_EDGE_SYNC_SECONDS", "15"))

    def loop():
        backoff = interval
        while True:
            r = syncer.sync_once()
            backoff = interval if r["ok"] else min(backoff * 2, 300)
            time.sleep(backoff)

    if start_loop:
        @app.on_event("startup")
        def _start():
            threading.Thread(target=loop, daemon=True, name="edge-sync").start()

    @app.exception_handler(service.EdgeError)
    async def _err(request: Request, exc: service.EdgeError):
        return JSONResponse({"error": "edge", "message": exc.message}, status_code=exc.status)

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
    def login(body: LoginIn):
        return service.login(store, body.username, body.password, body.register_id)

    @app.get("/edge/catalog")
    def catalog(authorization: str | None = Header(default=None)):
        service.session(store, tok(authorization))
        import json
        with store.connect() as c:
            items = []
            for r in c.execute("SELECT id, data FROM items"):
                it = json.loads(r["data"])
                it["available"] = str(service.available(store, c, r["id"]))
                items.append(it)
        return {"items": items, "policy": store.get_meta("policy"), "price_version": store.get_meta("price_version")}

    @app.post("/edge/sales")
    def sale(body: SaleIn, authorization: str | None = Header(default=None)):
        sess = service.session(store, tok(authorization))
        return service.sell(store, sess, client_key=body.client_key, lines=[x.model_dump() for x in body.lines],
                            payments=[x.model_dump() for x in body.payments])

    @app.get("/edge/sales")
    def sales(authorization: str | None = Header(default=None)):
        service.session(store, tok(authorization))
        with store.connect() as c:
            rows = c.execute("SELECT * FROM outbox ORDER BY created_at DESC LIMIT 100").fetchall()
        return {"sales": [service._receipt(r) | {"last_error": r["last_error"], "attempts": r["attempts"]} for r in rows]}

    @app.post("/edge/sync")
    def sync_now():
        return syncer.sync_once()

    page = Path(__file__).with_name("till.html")

    @app.get("/")
    def till():
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    return app


def _default_app():
    if os.environ.get("MOTION_EDGE_CENTRAL_URL") and os.environ.get("MOTION_EDGE_TOKEN"):
        return create_app()
    return None


app = _default_app()
