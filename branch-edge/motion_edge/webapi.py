"""The till API served by the branch box to the branch's checkout computers.

Same paths and response shapes as the head-office till endpoints the web app already uses,
so the one React till runs unchanged against the box. Browser sessions use an HttpOnly
cookie plus a CSRF token (double submit, checked against the session) like head office.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import service, till
from .service import EdgeError

SESSION_COOKIE = "motion_till"
CSRF_COOKIE = "motion_csrf"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class ShiftOpenIn(BaseModel):
    register_id: int = Field(gt=0)
    opening_float: str = Field(max_length=20)


class ShiftCloseIn(BaseModel):
    counted_cash: str = Field(max_length=20)
    cash_drop: str = Field(default="0", max_length=20)
    note: str | None = Field(default=None, max_length=500)


class TillLine(BaseModel):
    product_id: int = Field(gt=0)
    quantity: float | str
    discount_pct: float | str = 0


class TillPayment(BaseModel):
    method: str = Field(max_length=20)
    amount: float | str
    reference: str | None = Field(default=None, max_length=120)


class TillSaleIn(BaseModel):
    register_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=32, max_length=40)
    override_approval_id: int | None = None
    lines: list[TillLine] = Field(min_length=1, max_length=200)
    payments: list[TillPayment] = Field(max_length=6)


class OverrideIn(BaseModel):
    register_id: int = Field(gt=0)
    manager_username: str = Field(min_length=1, max_length=32)
    manager_password: str = Field(min_length=1, max_length=256)
    reason: str = Field(max_length=300)
    lines: list[TillLine] = Field(min_length=1, max_length=200)


class RefundLineIn(BaseModel):
    line_id: int = Field(gt=0)
    quantity: str = Field(max_length=20)


class EdgeRefundIn(BaseModel):
    sale_id: str = Field(min_length=32, max_length=40)
    lines: list[RefundLineIn] = Field(min_length=1, max_length=200)
    reason: str = Field(max_length=500)
    manager_username: str = Field(min_length=1, max_length=32)
    manager_password: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=32, max_length=40)


def _num(v) -> str:
    return format(v, "f") if isinstance(v, float) else str(v)


def build_router(app_state) -> APIRouter:
    store = app_state.store
    router = APIRouter()

    def wake() -> None:
        app_state.syncer.wake.set()  # forward to head office now, not at the next tick

    def session(request: Request, *, write: bool = False) -> dict:
        token = request.cookies.get(SESSION_COOKIE)
        sess = service.session(store, token)
        if write:
            sent = request.headers.get("x-csrf-token")
            if not sent or not sess.get("csrf") or sent != sess["csrf"] or request.cookies.get(CSRF_COOKIE) != sent:
                raise EdgeError("Your session security check failed. Reload the page.", 403)
        return sess

    @router.get("/api/meta")
    def meta():
        return till.meta(store)

    @router.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response):
        if not app_state.auth_workers.acquire(blocking=False):
            raise EdgeError("The till is busy signing in. Try again shortly.", 429)
        try:
            s = service.login(store, body.username, body.password, None,
                              client_ip=request.client.host if request.client else "unknown")
        finally:
            app_state.auth_workers.release()
        secure = request.url.scheme == "https"
        response.set_cookie(SESSION_COOKIE, s["token"], httponly=True, samesite="strict", secure=secure, path="/")
        response.set_cookie(CSRF_COOKIE, s["csrf"], httponly=False, samesite="strict", secure=secure, path="/")
        return {"ok": True}

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            with store.write() as c:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (service.sha(token),))
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"ok": True}

    @router.get("/api/auth/me")
    def me(request: Request):
        return till.me(store, session(request))

    @router.get("/api/pos/shift")
    def shift(request: Request):
        return {"shift": till.current_shift(store, session(request))}

    @router.post("/api/pos/shifts")
    def open_shift(body: ShiftOpenIn, request: Request):
        out = till.open_shift(store, session(request, write=True), register_id=body.register_id,
                              opening_float=body.opening_float)
        wake()
        return out

    @router.post("/api/pos/shifts/{shift_id}/close")
    def close_shift(shift_id: str, body: ShiftCloseIn, request: Request):
        out = till.close_shift(store, session(request, write=True), shift_id, counted_cash=body.counted_cash,
                               cash_drop=body.cash_drop, note=body.note)
        wake()
        return out

    @router.get("/api/pos/registers/{register_id}/catalog")
    def catalog(register_id: int, request: Request):
        session(request)
        return till.catalog(store, register_id)

    @router.get("/api/catalog/lookup")
    def lookup(code: str, request: Request):
        session(request)
        return till.lookup(store, code)

    @router.post("/api/pos/overrides")
    def override(body: OverrideIn, request: Request):
        sess = till.open_shift_for(store, session(request, write=True))["sess"]
        return till.request_override(store, sess, register_id=body.register_id,
                                     lines=[{"product_id": x.product_id, "discount_pct": _num(x.discount_pct)} for x in body.lines],
                                     manager_username=body.manager_username, manager_password=body.manager_password,
                                     reason=body.reason)

    @router.post("/api/pos/sales")
    def sale(body: TillSaleIn, request: Request):
        ctx = till.open_shift_for(store, session(request, write=True))
        if body.register_id != ctx["shift"]["register_id"]:
            raise EdgeError("Your shift is open on another till.", 409)
        row = service.sell(store, ctx["sess"], client_key=body.idempotency_key,
                           lines=[{"product_id": x.product_id, "quantity": _num(x.quantity),
                                   "discount_pct": _num(x.discount_pct)} for x in body.lines],
                           payments=[{"method": x.method, "amount": _num(x.amount), "reference": x.reference}
                                     for x in body.payments],
                           shift_id=ctx["shift"]["id"], override_id=body.override_approval_id)
        wake()
        with store.connect() as c:
            return till.receipt_view(store, c.execute("SELECT * FROM outbox WHERE event_id=?", (row["event_id"],)).fetchone())

    @router.get("/api/pos/receipts/{number}")
    def find(number: str, request: Request):
        session(request)
        return till.find_receipt(store, number)

    @router.post("/api/pos/refunds/at-till")
    def refund(body: EdgeRefundIn, request: Request):
        out = till.refund(store, session(request, write=True), sale_event_id=body.sale_id,
                          lines=[x.model_dump() for x in body.lines], reason=body.reason,
                          manager_username=body.manager_username, manager_password=body.manager_password,
                          client_key=body.idempotency_key)
        wake()
        return out

    @router.get("/api/approvals/mine")
    def mine(request: Request):
        return {"requests": till.recent_refunds(store, session(request))}

    @router.get("/api/edge/status")
    def status(request: Request):
        session(request)
        return service.status(store)

    @router.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def not_here(rest: str):
        return JSONResponse({"error": "not_available", "message": "This page is not available at the branch till."},
                            status_code=404)

    return router


def mount_web(app, web_dir: Path) -> None:
    """Serve the built web app (the same till screen) with an SPA fallback."""
    index = web_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str):
        if path.startswith(("api/", "edge/")):
            return JSONResponse({"error": "not_found", "message": "Unknown route."}, status_code=404)
        candidate = (web_dir / path).resolve()
        if path and candidate.is_file() and web_dir.resolve() in candidate.parents:
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        if not index.is_file():
            return JSONResponse({"error": "not_built", "message": "The till screen is not installed on this box."},
                                status_code=503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

