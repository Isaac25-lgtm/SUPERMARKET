"""Auth, session, reference data, users & access, audit trail."""
from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import tryton as t
from ..audit import record
from ..errors import Unauthenticated
from ..errors import NotFound
from ..security import (CSRF_COOKIE, SESSION_COOKIE, _LoginFailed, demo_accounts, demo_login, login, logout,
                        record_login_failure)
from ..services import approvals, common, users
from ..settings import get_settings
from ..web import MotionJSON, allow_pending_password, meta_of, unit_of_work

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


def _set_session_cookies(resp: Response, token: str, csrf: str, expires: dt.datetime) -> None:
    s = get_settings()
    max_age = int((expires - dt.datetime.now(dt.timezone.utc)).total_seconds())
    resp.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure, samesite="lax", path="/")
    resp.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure, samesite="lax", path="/")


@router.post("/auth/login")
def auth_login(body: LoginIn, request: Request):
    meta = meta_of(request)
    try:
        result = t.run(lambda: login(body.username, body.password, meta), user=0)
    except _LoginFailed as exc:
        record_login_failure(exc, meta)
        raise
    resp = MotionJSON({"user": result.principal.as_public(), "csrf_token": result.csrf_token})
    _set_session_cookies(resp, result.session_token, result.csrf_token, result.expires_at)
    return resp


class DemoLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


@router.get("/auth/demo-accounts")
def auth_demo_accounts():
    if not get_settings().open_demo:
        raise NotFound("Open demo is disabled.")
    return MotionJSON({"accounts": t.run(demo_accounts, readonly=True)})


@router.post("/auth/demo-login")
def auth_demo_login(body: DemoLoginIn, request: Request):
    if not get_settings().open_demo:
        raise NotFound("Open demo is disabled.")
    meta = meta_of(request)
    result = t.run(lambda: demo_login(body.username, meta), user=0)
    resp = MotionJSON({"user": result.principal.as_public(), "csrf_token": result.csrf_token})
    _set_session_cookies(resp, result.session_token, result.csrf_token, result.expires_at)
    return resp


@router.post("/auth/logout")
def auth_logout(request: Request):
    try:
        unit_of_work(request, allow_pending_password(lambda p: logout(p)))
    except Unauthenticated:
        pass
    resp = MotionJSON({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    resp.delete_cookie(CSRF_COOKIE, path="/")
    return resp


@router.get("/auth/me")
def auth_me(request: Request):
    def fn(p):
        out = p.as_public()
        out["branches"] = [b for b in common.branches() if p.can_branch(b["id"])]
        out["registers"] = [r for r in common.registers() if p.can_branch(r["branch_id"])]
        return out
    return MotionJSON(unit_of_work(request, allow_pending_password(fn), readonly=True))


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


@router.post("/auth/change-password")
def change_password(body: PasswordIn, request: Request):
    unit_of_work(request, allow_pending_password(
        lambda p: users.change_own_password(p, body.current_password, body.new_password)))
    return MotionJSON({"ok": True})


@router.get("/meta")
def meta(request: Request):
    """Public, non-sensitive configuration for the login screen."""
    s = get_settings()
    return MotionJSON({"brand": s.brand_name, "company": s.company_name, "currency": s.currency,
                       "timezone": s.timezone, "test_data": s.demo_mode, "ai_llm_enabled": s.ai_llm_enabled,
                       "open_demo": s.open_demo, "session_idle_minutes": s.session_idle_minutes,
                       "deployment": s.deployment})


@router.get("/branches")
def list_branches(request: Request):
    return MotionJSON(unit_of_work(request, lambda p: {
        # names only: lets a branch request stock *from* another branch without seeing its data
        "directory": [{"id": b["id"], "code": b["code"], "name": b["name"]} for b in common.branches()],
        "branches": [b for b in common.branches() if p.can_branch(b["id"])],
        "registers": [r for r in common.registers() if p.can_branch(r["branch_id"])]}))


# ------------------------------------------------------------------ admin: users & access

class UserCreateIn(BaseModel):
    username: str
    full_name: str
    email: str | None = None
    roles: list[str]
    branch_ids: list[int] = []
    all_branches: bool = False


class AccessIn(BaseModel):
    roles: list[str]
    branch_ids: list[int] = []
    all_branches: bool = False
    reason: str


class ReasonIn(BaseModel):
    reason: str


@router.get("/admin/users")
def admin_users(request: Request):
    return MotionJSON(unit_of_work(request, lambda p: {"users": users.list_users(p), "roles": users.roles_catalog(),
                                                        "branches": common.branches()}))


@router.get("/admin/users/{user_id}")
def admin_user(user_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: users.get_user(p, user_id)))


@router.post("/admin/users")
def admin_create_user(body: UserCreateIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: users.create_user(
        p, username=body.username, full_name=body.full_name, email=body.email, roles=body.roles,
        branch_ids=body.branch_ids, all_branches=body.all_branches)), status_code=201)


@router.put("/admin/users/{user_id}/access")
def admin_access(user_id: int, body: AccessIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: users.update_access(
        p, user_id, roles=body.roles, branch_ids=body.branch_ids, all_branches=body.all_branches, reason=body.reason)))


@router.post("/admin/users/{user_id}/suspend")
def admin_suspend(user_id: int, body: ReasonIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: users.set_status(p, user_id, status="suspended", reason=body.reason)))


@router.post("/admin/users/{user_id}/reactivate")
def admin_reactivate(user_id: int, body: ReasonIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: users.set_status(p, user_id, status="active", reason=body.reason)))


@router.post("/admin/users/{user_id}/revoke-sessions")
def admin_revoke(user_id: int, body: ReasonIn, request: Request):
    return MotionJSON({"revoked": unit_of_work(request, lambda p: users.revoke_sessions(p, user_id, body.reason))})


@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_pw(user_id: int, body: ReasonIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: users.reset_password(p, user_id, body.reason)))


@router.get("/admin/security-alerts")
def admin_alerts(request: Request):
    return MotionJSON({"alerts": unit_of_work(request, lambda p: users.security_alerts(p))})


class PolicyIn(BaseModel):
    min_amount: Decimal
    max_amount: Decimal | None = None
    steps: list[dict]
    reason: str


@router.get("/admin/policies")
def admin_policies(request: Request):
    return MotionJSON({"policies": unit_of_work(request, lambda p: approvals.list_policies(p))})


@router.put("/admin/policies/{policy_id}")
def admin_policy_update(policy_id: int, body: PolicyIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: approvals.replace_policy(
        p, policy_id, min_amount=body.min_amount, max_amount=body.max_amount, steps=body.steps, reason=body.reason)))


class DelegationIn(BaseModel):
    from_user: int
    to_user: int
    role_code: str
    max_amount: Decimal | None = None
    starts_at: dt.datetime
    ends_at: dt.datetime
    reason: str


@router.get("/admin/delegations")
def admin_delegations(request: Request):
    return MotionJSON({"delegations": unit_of_work(request, lambda p: approvals.list_delegations(p))})


@router.post("/admin/delegations")
def admin_delegation_create(body: DelegationIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: approvals.create_delegation(
        p, from_user=body.from_user, to_user=body.to_user, role_code=body.role_code, max_amount=body.max_amount,
        starts_at=body.starts_at, ends_at=body.ends_at, reason=body.reason)), status_code=201)


@router.post("/admin/delegations/{delegation_id}/revoke")
def admin_delegation_revoke(delegation_id: int, body: ReasonIn, request: Request):
    unit_of_work(request, lambda p: approvals.revoke_delegation(p, delegation_id, body.reason))
    return MotionJSON({"ok": True})


# ------------------------------------------------------------------ audit trail

AUDIT_SQL = """
SELECT e.id, e.at, e.actor_id, e.actor_username, e.actor_roles, e.branch_id, b.code AS branch_code, e.action,
       e.outcome, e.target_type, e.target_id, e.amount, e.reason, e.before_state, e.after_state, e.request_id,
       e.ip, e.session_ref
FROM motion.audit_event e LEFT JOIN motion_branch b ON b.id = e.branch_id
WHERE (%(q)s::text IS NULL OR e.action ILIKE %(ql)s OR e.target_id ILIKE %(ql)s OR e.actor_username ILIKE %(ql)s
       OR e.reason ILIKE %(ql)s)
  AND (%(actor)s::bigint IS NULL OR e.actor_id = %(actor)s)
  AND (%(branch)s::int IS NULL OR e.branch_id = %(branch)s)
  AND (%(action)s::text IS NULL OR e.action LIKE %(action_prefix)s)
  AND (%(outcome)s::text IS NULL OR e.outcome = %(outcome)s)
  AND (%(target)s::text IS NULL OR e.target_id = %(target)s)
  AND (%(min_amount)s::numeric IS NULL OR abs(e.amount) >= %(min_amount)s)
  AND e.at >= %(date_from)s AND e.at < %(date_to)s
  AND (%(all_branches)s OR e.branch_id = ANY(%(branches)s) OR (e.branch_id IS NULL AND %(see_global)s))
ORDER BY e.at DESC, e.id DESC
LIMIT %(limit)s OFFSET %(offset)s
"""


def _audit_params(p, q, actor, branch, action, outcome, target, min_amount, date_from, date_to, limit, offset):
    p.require("audit.view", action="audit.search")
    if branch is not None:
        p.require_branch(branch, action="audit.search")
    tz = common.local_tz()
    df = dt.datetime.combine(date_from or (common.today() - dt.timedelta(days=30)), dt.time.min, tz)
    dt_ = dt.datetime.combine((date_to or common.today()) + dt.timedelta(days=1), dt.time.min, tz)
    return {"q": q, "ql": f"%{q}%" if q else None, "actor": actor, "branch": branch, "action": action,
            "action_prefix": f"{action}%" if action else None, "outcome": outcome, "target": target,
            "min_amount": min_amount, "date_from": df, "date_to": dt_, "all_branches": p.all_branches,
            "branches": sorted(p.branch_ids), "see_global": p.all_branches, "limit": limit, "offset": offset}


@router.get("/audit")
def audit_search(request: Request, q: str | None = None, actor: int | None = None, branch: int | None = None,
                 action: str | None = None, outcome: str | None = None, target: str | None = None,
                 min_amount: Decimal | None = None, date_from: dt.date | None = None, date_to: dt.date | None = None,
                 limit: int = Query(100, le=500), offset: int = 0):
    def fn(p):
        params = _audit_params(p, q, actor, branch, action, outcome, target, min_amount, date_from, date_to, limit, offset)
        rows = t.sql(AUDIT_SQL, params)
        scope = "(%(all_branches)s OR e.branch_id = ANY(%(branches)s) OR (e.branch_id IS NULL AND %(see_global)s))"
        actors = t.sql(f"""SELECT DISTINCT u.id, u.username, u.full_name FROM motion.audit_event e
                           JOIN motion.app_user u ON u.id = e.actor_id WHERE {scope} ORDER BY u.full_name""", params)
        actions = [r["action"] for r in t.sql(
            f"SELECT DISTINCT e.action FROM motion.audit_event e WHERE {scope} ORDER BY 1", params)]
        return {"events": rows, "actors": actors, "actions": actions}
    return MotionJSON(unit_of_work(request, fn))


@router.get("/audit/export.csv")
def audit_export(request: Request, q: str | None = None, actor: int | None = None, branch: int | None = None,
                 action: str | None = None, outcome: str | None = None, date_from: dt.date | None = None,
                 date_to: dt.date | None = None):
    def fn(p):
        p.require("audit.export", action="audit.export")
        params = _audit_params(p, q, actor, branch, action, outcome, None, None, date_from, date_to, 10000, 0)
        rows = t.sql(AUDIT_SQL, params)
        record(actor=p, action="audit.export", reason=f"{len(rows)} rows",
               after={"filters": {k: v for k, v in params.items() if k in {"q", "actor", "branch", "action", "outcome"}}})
        return rows
    rows = unit_of_work(request, fn, csrf=False)
    buf = io.StringIO()
    w = csv.writer(buf)
    # Data minimisation: no IPs/user agents/session refs in exports.
    w.writerow(["at_utc", "actor", "roles", "branch", "action", "outcome", "target_type", "target_id", "amount", "reason"])
    for r in rows:
        w.writerow([r["at"].isoformat(), r["actor_username"], " ".join(r["actor_roles"] or []), r["branch_code"],
                    r["action"], r["outcome"], r["target_type"], r["target_id"], r["amount"], r["reason"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=audit-trail.csv"})
