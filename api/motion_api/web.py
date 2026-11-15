"""HTTP plumbing shared by routers: request meta, unit-of-work, JSON encoding."""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import tryton as t
from .audit import RequestMeta
from .errors import AppError, Forbidden
from .security import CSRF_COOKIE, SESSION_COOKIE, Principal, audit_denied, authenticate_session, check_csrf

logger = logging.getLogger("motion.web")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _default(o: Any):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"not serializable: {type(o)}")


class MotionJSON(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(content, default=_default, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def meta_of(request: Request) -> RequestMeta:
    fwd = request.headers.get("x-forwarded-for")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)
    return RequestMeta(request_id=request.state.request_id if hasattr(request.state, "request_id") else None,
                       ip=ip, user_agent=request.headers.get("user-agent"))


def unit_of_work(request: Request, fn: Callable[[Principal], Any], *, readonly: bool = False,
                 csrf: bool | None = None) -> Any:
    """Authenticate + authorize + execute ``fn`` in one Tryton transaction."""
    meta = meta_of(request)
    needs_csrf = request.method in UNSAFE if csrf is None else csrf
    holder: dict[str, Principal | None] = {"p": None}

    def work():
        principal = authenticate_session(request.cookies.get(SESSION_COOKIE), meta)
        holder["p"] = principal
        if needs_csrf:
            check_csrf(principal, request.headers.get("x-csrf-token"), request.cookies.get(CSRF_COOKIE))
        if principal.must_change_password and not getattr(fn, "allow_password_change_pending", False):
            from .errors import Forbidden as F

            raise F("Change your temporary password before continuing.", action="auth.password_change_required")
        return fn(principal)

    try:
        # Session touch writes, so even GETs run read-write; business reads stay side-effect free.
        route = request.scope.get("route")
        op_key = f"{request.method} {getattr(route, 'path', request.url.path)}"
        return t.run(work, user=0, readonly=False, op_key=op_key)
    except Forbidden as exc:
        audit_denied(exc, holder["p"], meta)
        raise


def allow_pending_password(fn):
    fn.allow_password_change_pending = True
    return fn


def error_response(exc: AppError) -> Response:
    body = {"error": exc.code, "message": exc.message}
    if exc.details:
        body["details"] = exc.details
    return MotionJSON(body, status_code=exc.status)
