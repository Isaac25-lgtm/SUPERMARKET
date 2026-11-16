"""HTTP plumbing shared by routers: request meta, unit-of-work, JSON encoding."""
from __future__ import annotations

import csv
import datetime as dt
import io
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
from .security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    Principal,
    _LoginFailed,
    audit_denied,
    authenticate_session,
    check_csrf,
    record_login_failure,
    touch_session,
)

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
    ip = request.client.host if request.client else None
    return RequestMeta(request_id=request.state.request_id if hasattr(request.state, "request_id") else None,
                       ip=ip, user_agent=request.headers.get("user-agent"))


def csv_download(filename: str, header: list[str], rows) -> Response:
    def cell(value):
        if isinstance(value, str) and (value[:1] in {"\t", "\r", "\n"}
                                      or value.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠"))):
            return "'" + value
        return value

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows([cell(value) for value in row] for row in rows)
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


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
        route = request.scope.get("route")
        op_key = f"{request.method} {route.path}" if hasattr(route, "path") else None
        result = t.run(work, user=0, readonly=readonly, op_key=op_key)
    except _LoginFailed as exc:
        record_login_failure(exc, meta)
        raise Forbidden("Manager credentials were not accepted.", action=exc.action) from None
    except Forbidden as exc:
        audit_denied(exc, holder["p"], meta)
        raise
    principal = holder["p"]
    if principal and principal.session_touch_due and principal.session_hash:
        session_hash = principal.session_hash
        try:
            t.run(lambda: touch_session(session_hash), retries=2)
        except Exception:
            logger.warning("Could not refresh session activity for request %s", meta.request_id)
    return result


def allow_pending_password(fn):
    fn.allow_password_change_pending = True
    return fn


def error_response(exc: AppError) -> Response:
    body = {"error": exc.code, "message": exc.message}
    if exc.details:
        body["details"] = exc.details
    return MotionJSON(body, status_code=exc.status)
