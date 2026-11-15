"""Users & access administration (Head Manager)."""
from __future__ import annotations

import re
import secrets

from .. import tryton as t
from ..audit import record
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..permissions import ROLE_CODES, ROLES
from ..security import Principal, hash_password, revoke_user_sessions, validate_new_password, verify_password
from . import common

USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")


def _tryton_identity(full_name: str, username: str) -> tuple[int, int]:
    """Create the Tryton employee + res.user used for record attribution.

    The Tryton user has no password, so it cannot log into Tryton directly;
    Tryton's RPC server is not exposed at all in this deployment.
    """
    Party = t.model("party.party")
    Employee = t.model("company.employee")
    User = t.model("res.user")
    cid = common.company_id()
    party, = Party.create([{"name": full_name}])
    employee, = Employee.create([{"party": party.id, "company": cid}])
    login = f"motion.{username}"
    existing = User.search([("login", "=", login)])
    if existing:
        user = existing[0]
    else:
        user, = User.create([{"name": full_name, "login": login, "companies": [("add", [cid])], "company": cid,
                              "employees": [("add", [employee.id])], "employee": employee.id}])
    return user.id, employee.id


def _require_company_admin(actor: Principal, action: str) -> None:
    """H1: user administration is company-wide authority (all-branch head office)."""
    actor.require("admin.users", action=action)
    if not actor.all_branches:
        raise Forbidden("User administration is company-wide; it needs all-branch (head office) access.", action=action)


def _user_row(user_id: int) -> dict:
    row = t.sql_one("SELECT * FROM motion.app_user WHERE id=%s", (user_id,))
    if not row:
        raise NotFound("User not found.")
    return row


def _snapshot(user_id: int) -> dict:
    row = _user_row(user_id)
    roles = sorted(r["role_code"] for r in t.sql("SELECT role_code FROM motion.user_role WHERE user_id=%s", (user_id,)))
    branches = sorted(r["branch_id"] for r in t.sql("SELECT branch_id FROM motion.user_branch WHERE user_id=%s", (user_id,)))
    return {"username": row["username"], "full_name": row["full_name"], "status": row["status"],
            "roles": roles, "branch_ids": branches, "all_branches": row["all_branches"]}


def _validate_grants(actor: Principal, roles: list[str], branch_ids: list[int], all_branches: bool) -> None:
    unknown = set(roles) - ROLE_CODES
    if unknown:
        raise Invalid(f"Unknown roles: {', '.join(sorted(unknown))}")
    if not roles:
        raise Invalid("Assign at least one role.")
    if "owner" in roles and "owner" not in actor.roles:
        raise Forbidden("Only a Head Manager can grant the Head Manager role.", action="admin.users.grant_owner")
    if "sysadmin" in roles and len(roles) > 1:
        raise Invalid("The technical administrator role cannot be combined with business roles.")
    valid = set(common.branch_ids())
    bad = set(branch_ids) - valid
    if bad:
        raise Invalid("Unknown branch selected.")
    if not all_branches and not branch_ids and "sysadmin" not in roles:
        raise Invalid("Assign at least one branch or 'all branches'.")


def list_users(actor: Principal) -> list[dict]:
    _require_company_admin(actor, "admin.users")
    rows = t.sql("""
        SELECT u.id, u.username, u.full_name, u.email, u.status, u.all_branches, u.last_login_at,
               u.failed_logins, u.locked_until, u.must_change_password, u.created_at,
               COALESCE((SELECT array_agg(role_code ORDER BY role_code) FROM motion.user_role r WHERE r.user_id=u.id), '{}') AS roles,
               COALESCE((SELECT array_agg(branch_id ORDER BY branch_id) FROM motion.user_branch b WHERE b.user_id=u.id), '{}') AS branch_ids,
               (SELECT count(*) FROM motion.session s WHERE s.user_id=u.id AND s.revoked_at IS NULL AND s.expires_at > now()) AS active_sessions,
               (SELECT count(*) FROM motion.login_attempt a WHERE lower(a.username)=lower(u.username) AND NOT a.success AND a.at > now() - interval '7 days') AS failures_7d
        FROM motion.app_user u ORDER BY u.status, u.full_name""")
    return rows


def get_user(actor: Principal, user_id: int) -> dict:
    _require_company_admin(actor, "admin.users")
    users = [u for u in list_users(actor) if u["id"] == user_id]
    if not users:
        raise NotFound("User not found.")
    user = users[0]
    user["history"] = t.sql("""
        SELECT at, action, outcome, actor_username, reason, before_state, after_state
        FROM motion.audit_event WHERE target_type='user' AND target_id=%s ORDER BY at DESC LIMIT 50""", (str(user_id),))
    user["recent_activity"] = t.sql("""
        SELECT at, action, outcome, target_type, target_id, amount FROM motion.audit_event
        WHERE actor_id=%s ORDER BY at DESC LIMIT 30""", (user_id,))
    return user


def create_user(actor: Principal, *, username: str, full_name: str, email: str | None, roles: list[str],
                branch_ids: list[int], all_branches: bool, temporary_password: str | None = None,
                must_change_password: bool = True) -> dict:
    _require_company_admin(actor, "admin.users.create")
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise Invalid("Username must be 3-32 chars: lowercase letters, digits, dot, dash or underscore.")
    if not full_name.strip():
        raise Invalid("Full name is required.")
    _validate_grants(actor, roles, branch_ids, all_branches)
    if t.sql_one("SELECT 1 AS x FROM motion.app_user WHERE lower(username)=%s", (username,)):
        raise Conflict("That username is already taken.")
    generated = temporary_password is None
    password = temporary_password or (secrets.token_urlsafe(9) + "7a")
    validate_new_password(password, username)
    tryton_user, employee = _tryton_identity(full_name.strip(), username)
    row = t.sql_one("""
        INSERT INTO motion.app_user (username, full_name, email, password_hash, all_branches, must_change_password,
                                     tryton_user_id, tryton_employee_id, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (username, full_name.strip(), email, hash_password(password), all_branches, must_change_password,
         tryton_user, employee, actor.user_id))
    uid = row["id"]
    for r in roles:
        t.execute("INSERT INTO motion.user_role (user_id, role_code, granted_by) VALUES (%s,%s,%s)", (uid, r, actor.user_id))
    for b in set(branch_ids):
        t.execute("INSERT INTO motion.user_branch (user_id, branch_id, granted_by) VALUES (%s,%s,%s)", (uid, b, actor.user_id))
    record(actor=actor, action="admin.user.create", target_type="user", target_id=uid, after=_snapshot(uid))
    return {"id": uid, "username": username, "temporary_password": password if generated else None}


def update_access(actor: Principal, user_id: int, *, roles: list[str], branch_ids: list[int], all_branches: bool,
                  reason: str) -> dict:
    _require_company_admin(actor, "admin.user.access")
    if user_id == actor.user_id:
        raise Forbidden("You cannot change your own roles or branch access.", action="admin.user.access.self",
                        target_type="user", target_id=str(user_id))
    if not reason or len(reason.strip()) < 3:
        raise Invalid("Give a reason for the access change.")
    _validate_grants(actor, roles, branch_ids, all_branches)
    before = _snapshot(user_id)
    if "owner" in before["roles"] and "owner" not in actor.roles:
        raise Forbidden("Only a Head Manager can change another Head Manager.", action="admin.user.access")
    t.execute("DELETE FROM motion.user_role WHERE user_id=%s", (user_id,))
    t.execute("DELETE FROM motion.user_branch WHERE user_id=%s", (user_id,))
    for r in roles:
        t.execute("INSERT INTO motion.user_role (user_id, role_code, granted_by) VALUES (%s,%s,%s)", (user_id, r, actor.user_id))
    for b in set(branch_ids):
        t.execute("INSERT INTO motion.user_branch (user_id, branch_id, granted_by) VALUES (%s,%s,%s)", (user_id, b, actor.user_id))
    t.execute("UPDATE motion.app_user SET all_branches=%s, updated_at=now() WHERE id=%s", (all_branches, user_id))
    after = _snapshot(user_id)
    # Reduced privileges take effect immediately: existing sessions are revoked.
    revoked = revoke_user_sessions(user_id, "access_changed")
    record(actor=actor, action="admin.user.access", target_type="user", target_id=user_id, reason=reason,
           before=before, after={**after, "sessions_revoked": revoked})
    return after


def set_status(actor: Principal, user_id: int, *, status: str, reason: str) -> dict:
    _require_company_admin(actor, f"admin.user.{status}")
    if status not in {"active", "suspended"}:
        raise Invalid("Unknown status.")
    if user_id == actor.user_id:
        raise Forbidden("You cannot change your own account status.", action="admin.user.status.self",
                        target_type="user", target_id=str(user_id))
    if not reason or len(reason.strip()) < 3:
        raise Invalid("Give a reason.")
    before = _snapshot(user_id)
    if "owner" in before["roles"] and "owner" not in actor.roles:
        raise Forbidden("Only a Head Manager can change another Head Manager.", action="admin.user.status")
    t.execute("UPDATE motion.app_user SET status=%s, updated_at=now(), failed_logins=0, locked_until=NULL WHERE id=%s",
              (status, user_id))
    revoked = revoke_user_sessions(user_id, "suspended") if status == "suspended" else 0
    record(actor=actor, action="admin.user.suspend" if status == "suspended" else "admin.user.reactivate",
           target_type="user", target_id=user_id, reason=reason, before={"status": before["status"]},
           after={"status": status, "sessions_revoked": revoked})
    return _snapshot(user_id)


def revoke_sessions(actor: Principal, user_id: int, reason: str) -> int:
    _require_company_admin(actor, "admin.user.revoke_sessions")
    _user_row(user_id)
    n = revoke_user_sessions(user_id, "admin_revoked")
    record(actor=actor, action="admin.user.revoke_sessions", target_type="user", target_id=user_id, reason=reason,
           after={"sessions_revoked": n})
    return n


def reset_password(actor: Principal, user_id: int, reason: str) -> dict:
    _require_company_admin(actor, "admin.user.reset_password")
    if user_id == actor.user_id:
        raise Forbidden("Use 'Change password' for your own account.", action="admin.user.reset_password.self")
    row = _user_row(user_id)
    password = secrets.token_urlsafe(9) + "7a"
    t.execute("UPDATE motion.app_user SET password_hash=%s, must_change_password=true, failed_logins=0, "
              "locked_until=NULL, updated_at=now() WHERE id=%s", (hash_password(password), user_id))
    revoke_user_sessions(user_id, "password_reset")
    record(actor=actor, action="admin.user.reset_password", target_type="user", target_id=user_id, reason=reason)
    return {"username": row["username"], "temporary_password": password}


def change_own_password(actor: Principal, current: str, new: str) -> None:
    row = _user_row(actor.user_id)
    if not verify_password(row["password_hash"], current):
        raise Invalid("Current password is incorrect.")
    validate_new_password(new, row["username"])
    if verify_password(row["password_hash"], new):
        raise Invalid("Choose a password different from the current one.")
    t.execute("UPDATE motion.app_user SET password_hash=%s, must_change_password=false, password_changed_at=now() "
              "WHERE id=%s", (hash_password(new), actor.user_id))
    # Other sessions are ended; the current one stays.
    t.execute("UPDATE motion.session SET revoked_at=now(), revoked_reason='password_changed' "
              "WHERE user_id=%s AND id_hash<>%s AND revoked_at IS NULL", (actor.user_id, actor.session_hash))
    record(actor=actor, action="auth.password_changed", target_type="user", target_id=actor.user_id)


def roles_catalog() -> list[dict]:
    return [{"code": r.code, "name": r.name, "description": r.description, "hq": r.hq} for r in ROLES]


def security_alerts(actor: Principal) -> list[dict]:
    """Rule-based security review flags for the Head Manager."""
    _require_company_admin(actor, "admin.users")
    alerts = []
    for r in t.sql("""
        SELECT lower(username) AS username, count(*) AS n, max(at) AS last_at FROM motion.login_attempt
        WHERE NOT success AND at > now() - interval '24 hours' GROUP BY 1 HAVING count(*) >= 3 ORDER BY n DESC"""):
        alerts.append({"kind": "failed_logins", "severity": "high" if r["n"] >= 5 else "medium",
                       "title": f"{r['n']} failed sign-ins for '{r['username']}' in 24h", "at": r["last_at"]})
    for r in t.sql("""
        SELECT actor_username, count(*) AS n, max(at) AS last_at FROM motion.audit_event
        WHERE action LIKE 'admin.user.access%%' AND at > now() - interval '24 hours' GROUP BY 1 HAVING count(*) >= 3"""):
        alerts.append({"kind": "privilege_changes", "severity": "medium",
                       "title": f"{r['n']} access changes by {r['actor_username']} in 24h", "at": r["last_at"]})
    for r in t.sql("""
        SELECT actor_username, count(*) AS n, max(at) AS last_at FROM motion.audit_event
        WHERE outcome='denied' AND actor_id IS NOT NULL AND at > now() - interval '24 hours'
        GROUP BY 1 HAVING count(*) >= 3 ORDER BY n DESC"""):
        alerts.append({"kind": "denied_actions", "severity": "medium",
                       "title": f"{r['n']} denied actions by {r['actor_username']} in 24h", "at": r["last_at"]})
    for r in t.sql("""
        SELECT u.full_name, count(*) AS n, max(s.posted_at) AS last_at FROM motion.pos_sale s
        JOIN motion.app_user u ON u.id = s.cashier_id
        WHERE s.kind='refund' AND s.posted_at > now() - interval '7 days' GROUP BY 1 HAVING count(*) >= 3"""):
        alerts.append({"kind": "refund_frequency", "severity": "medium",
                       "title": f"{r['n']} refunds processed by {r['full_name']} in 7 days", "at": r["last_at"]})
    return alerts
