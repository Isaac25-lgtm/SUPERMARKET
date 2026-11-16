"""Authentication, sessions, CSRF and the authorization principal.

* Passwords: Argon2id (argon2-cffi).  Never logged, never returned.
* Sessions: random 256-bit token in an HttpOnly cookie; only its SHA-256 is
  stored.  Absolute TTL + idle timeout; suspension or "revoke all" kills them.
* CSRF: double-submit token (cookie + X-CSRF-Token header) whose hash must
  match the session's csrf_hash, required on every state-changing request.
* Brute force: per-username lockout after N failures and a per-IP ceiling.
* Authorization: :class:`Principal` carries roles → permissions and the
  explicit branch scope; every service call checks both.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from . import tryton as t
from .audit import RequestMeta, record, record_detached
from .errors import Forbidden, Invalid, RateLimited, Unauthenticated
from .permissions import permissions_for
from .settings import get_settings

_hasher = PasswordHasher()
SESSION_COOKIE = "motion_session"
CSRF_COOKIE = "motion_csrf"
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")
COMMON_PASSWORDS = {"password", "password1", "123456789", "1234567890", "qwerty123", "letmein123",
                    "supermarket", "welcome123", "admin12345", "motion12345"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and bool(stored_hash)
    except (VerificationError, InvalidHashError):
        return False


def validate_new_password(password: str, username: str) -> None:
    if len(password) < 10:
        raise Invalid("Password must be at least 10 characters.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise Invalid("Password must contain letters and digits.")
    if password.lower() in COMMON_PASSWORDS or username.lower() in password.lower():
        raise Invalid("Password is too easy to guess.")


@dataclass
class Principal:
    user_id: int
    username: str
    full_name: str
    roles: set[str]
    permissions: set[str]
    all_branches: bool
    branch_ids: set[int]
    tryton_user_id: int | None
    tryton_employee_id: int | None
    must_change_password: bool
    session_hash: str | None = None
    session_touch_due: bool = False
    meta: RequestMeta = field(default_factory=RequestMeta)

    def has(self, perm: str) -> bool:
        return perm in self.permissions

    def can_branch(self, branch_id: int | None) -> bool:
        if branch_id is None:
            return False
        return self.all_branches or int(branch_id) in self.branch_ids

    def require(self, perm: str, *, action: str | None = None) -> None:
        if perm not in self.permissions:
            raise Forbidden(f"Your role does not allow this action ({perm}).", action=action or perm)

    def require_any(self, *perms: str) -> None:
        if not any(p in self.permissions for p in perms):
            raise Forbidden("Your role does not allow this action.", action=perms[0])

    def require_branch(self, branch_id: int | None, *, action: str = "branch_access",
                       target_type: str | None = None, target_id=None) -> int:
        if branch_id is None or not self.can_branch(branch_id):
            raise Forbidden("You are not assigned to this branch.", action=action, target_type=target_type,
                            target_id=None if target_id is None else str(target_id), branch_id=branch_id)
        return int(branch_id)

    def scoped_branch_ids(self, all_ids: list[int]) -> list[int]:
        return list(all_ids) if self.all_branches else [b for b in all_ids if b in self.branch_ids]

    def as_public(self) -> dict:
        return {
            "id": self.user_id, "username": self.username, "full_name": self.full_name,
            "roles": sorted(self.roles), "permissions": sorted(self.permissions),
            "all_branches": self.all_branches, "branch_ids": sorted(self.branch_ids),
            "must_change_password": self.must_change_password,
        }


def load_principal(user_id: int, *, session_hash: str | None = None, meta: RequestMeta | None = None) -> Principal:
    row = t.sql_one("SELECT * FROM motion.app_user WHERE id=%s", (user_id,))
    if not row or row["status"] != "active":
        raise Unauthenticated("Account is not active.")
    roles = {r["role_code"] for r in t.sql("SELECT role_code FROM motion.user_role WHERE user_id=%s", (user_id,))}
    branches = {r["branch_id"] for r in t.sql("SELECT branch_id FROM motion.user_branch WHERE user_id=%s", (user_id,))}
    return Principal(
        user_id=row["id"], username=row["username"], full_name=row["full_name"], roles=roles,
        permissions=permissions_for(roles), all_branches=row["all_branches"], branch_ids=branches,
        tryton_user_id=row["tryton_user_id"], tryton_employee_id=row["tryton_employee_id"],
        must_change_password=row["must_change_password"], session_hash=session_hash, meta=meta or RequestMeta(),
    )


def authenticate_session(token: str | None, meta: RequestMeta) -> Principal:
    """Validate the session cookie inside the current transaction."""
    if not token:
        raise Unauthenticated("Please sign in.")
    settings = get_settings()
    id_hash = sha256(token)
    row = t.sql_one(
        "SELECT s.*, u.status FROM motion.session s JOIN motion.app_user u ON u.id = s.user_id WHERE s.id_hash=%s",
        (id_hash,))
    now = utcnow()
    if not row or row["revoked_at"] is not None or row["expires_at"] <= now or row["status"] != "active":
        raise Unauthenticated("Your session has ended. Please sign in again.")
    if row["origin"] == "demo" and not settings.open_demo:
        # C2: switching open demo off ends every demo session immediately.
        # Persist the revocation in its own transaction: this one rolls back when we raise.
        with t.transaction().new_transaction() as tr:
            tr.connection.cursor().execute(
                "UPDATE motion.session SET revoked_at=now(), revoked_reason='demo_closed' WHERE id_hash=%s", (id_hash,))
        raise Unauthenticated("Demo access has ended. Please sign in.")
    if row["last_seen_at"] + timedelta(minutes=settings.session_idle_minutes) <= now:
        raise Unauthenticated("Signed out after inactivity. Please sign in again.")
    meta.session_ref = id_hash[:12]
    principal = load_principal(row["user_id"], session_hash=id_hash, meta=meta)
    principal.session_touch_due = now - row["last_seen_at"] > timedelta(seconds=60)
    return principal


def touch_session(session_hash: str) -> None:
    t.execute("""UPDATE motion.session SET last_seen_at=now() WHERE id_hash=%s AND revoked_at IS NULL
                 AND expires_at>now() AND last_seen_at<now()-interval '60 seconds'""", (session_hash,))


def check_csrf(principal: Principal, header_token: str | None, cookie_token: str | None) -> None:
    if not header_token or not cookie_token or not hmac.compare_digest(header_token, cookie_token):
        raise Forbidden("Missing or invalid CSRF token.", action="csrf")
    row = t.sql_one("SELECT csrf_hash FROM motion.session WHERE id_hash=%s", (principal.session_hash,))
    if not row or not hmac.compare_digest(row["csrf_hash"], sha256(header_token)):
        raise Forbidden("Missing or invalid CSRF token.", action="csrf")


@dataclass
class LoginResult:
    principal: Principal
    session_token: str
    csrf_token: str
    expires_at: datetime


def verify_credentials(username: str, password: str, meta: RequestMeta, *, action: str = "auth.login") -> dict:
    uname = (username or "").strip().lower()
    recent_ip_failures = t.sql_one(
        "SELECT count(*) AS n FROM motion.login_attempt WHERE ip=%s AND NOT success AND at > now() - interval '15 minutes'",
        (meta.ip,))["n"] if meta.ip else 0
    if recent_ip_failures >= 30:
        raise RateLimited("Too many failed sign-in attempts from this network. Try again later.")
    row = t.sql_one("SELECT * FROM motion.app_user WHERE lower(username)=%s", (uname,))
    if row and row["locked_until"] and row["locked_until"] > utcnow():
        raise _LoginFailed(uname, "locked", row["id"], "Account temporarily locked after repeated failures.", action)
    ok = verify_password(row["password_hash"] if row else None, password)
    if not row or not ok:
        # The failure counter is updated by record_login_failure() in its own
        # transaction: this one rolls back when the exception propagates.
        raise _LoginFailed(uname, "bad_credentials", row["id"] if row else None, "Incorrect username or password.", action)
    if row["status"] != "active":
        raise _LoginFailed(uname, "suspended", row["id"], "This account is suspended. Contact your manager.", action)
    return row


def login(username: str, password: str, meta: RequestMeta) -> LoginResult:
    """Verify credentials and open a session (runs inside a transaction)."""
    row = verify_credentials(username, password, meta)
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = utcnow() + timedelta(minutes=settings.session_ttl_minutes)
    t.execute(
        "INSERT INTO motion.session (id_hash, user_id, csrf_hash, expires_at, ip, user_agent) VALUES (%s,%s,%s,%s,%s,%s)",
        (sha256(token), row["id"], sha256(csrf), expires, meta.ip, (meta.user_agent or "")[:300]))
    t.execute("UPDATE motion.app_user SET failed_logins=0, locked_until=NULL, last_login_at=now() WHERE id=%s", (row["id"],))
    t.execute("INSERT INTO motion.login_attempt (username, ip, success) VALUES (%s,%s,true)", (row["username"], meta.ip))
    meta.session_ref = sha256(token)[:12]
    principal = load_principal(row["id"], session_hash=sha256(token), meta=meta)
    record(actor=principal, action="auth.login", target_type="user", target_id=row["id"])
    return LoginResult(principal, token, csrf, expires)


def open_session(user_id: int, meta: RequestMeta, *, action: str, origin: str = "password") -> LoginResult:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = utcnow() + timedelta(minutes=settings.session_ttl_minutes)
    t.execute(
        "INSERT INTO motion.session (id_hash, user_id, csrf_hash, expires_at, ip, user_agent, origin) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (sha256(token), user_id, sha256(csrf), expires, meta.ip, (meta.user_agent or "")[:300], origin))
    t.execute("UPDATE motion.app_user SET last_login_at=now() WHERE id=%s", (user_id,))
    meta.session_ref = sha256(token)[:12]
    principal = load_principal(user_id, session_hash=sha256(token), meta=meta)
    record(actor=principal, action=action, target_type="user", target_id=user_id)
    return LoginResult(principal, token, csrf, expires)


def demo_accounts() -> list[dict]:
    """Accounts offered by the open-demo picker (no secrets)."""
    return t.sql("""
        SELECT u.id, u.username, u.full_name, u.all_branches,
               COALESCE((SELECT array_agg(role_code ORDER BY role_code) FROM motion.user_role r WHERE r.user_id=u.id), '{}') AS roles,
               COALESCE((SELECT array_agg(b.code ORDER BY b.code) FROM motion.user_branch ub
                         JOIN motion_branch b ON b.id = ub.branch_id WHERE ub.user_id=u.id), '{}') AS branches
        FROM motion.app_user u WHERE u.is_demo AND u.status='active' AND NOT u.must_change_password ORDER BY u.id""")


def demo_login(username: str, meta: RequestMeta) -> LoginResult:
    if not get_settings().open_demo:
        raise Forbidden("Open demo sign-in is disabled.", action="auth.demo_login.disabled")
    row = t.sql_one("SELECT * FROM motion.app_user WHERE lower(username)=%s", ((username or "").strip().lower(),))
    # C2: only accounts explicitly marked as demo accounts can be opened without a password.
    if not row or not row["is_demo"] or row["status"] != "active" or row["must_change_password"]:
        raise Unauthenticated("That account is not available in the demo.")
    return open_session(row["id"], meta, action="auth.demo_login", origin="demo")


class _LoginFailed(Unauthenticated):
    def __init__(self, username: str, reason: str, user_id: int | None, message: str, action: str = "auth.login"):
        super().__init__(message)
        self.username = username
        self.reason = reason
        self.user_id = user_id
        self.action = action


def record_login_failure(exc: _LoginFailed, meta: RequestMeta) -> None:
    settings = get_settings()

    def work():
        t.execute("INSERT INTO motion.login_attempt (username, ip, success, reason) VALUES (%s,%s,false,%s)",
                  (exc.username, meta.ip, exc.reason))
        if exc.user_id and exc.reason == "bad_credentials":
            row = t.sql_one("SELECT failed_logins FROM motion.app_user WHERE id=%s FOR UPDATE", (exc.user_id,))
            failures = row["failed_logins"] + 1
            if failures >= settings.login_max_failures:
                t.execute("""UPDATE motion.app_user SET failed_logins=0,
                             locked_until=now() + make_interval(mins => %s) WHERE id=%s""",
                          (settings.login_lock_minutes, exc.user_id))
            else:
                t.execute("UPDATE motion.app_user SET failed_logins=%s WHERE id=%s", (failures, exc.user_id))
        record(action=exc.action, outcome="denied", target_type="user", target_id=exc.user_id,
               reason=f"{exc.reason}: {exc.username}", meta=meta)
    t.run(work, user=0)


def logout(principal: Principal) -> None:
    t.execute("UPDATE motion.session SET revoked_at=now(), revoked_reason='logout' WHERE id_hash=%s",
              (principal.session_hash,))
    record(actor=principal, action="auth.logout", target_type="user", target_id=principal.user_id)


def revoke_user_sessions(user_id: int, reason: str) -> int:
    return t.execute(
        "UPDATE motion.session SET revoked_at=now(), revoked_reason=%s WHERE user_id=%s AND revoked_at IS NULL",
        (reason, user_id))


def audit_denied(exc: Forbidden, principal: Principal | None, meta: RequestMeta) -> None:
    record_detached(actor=principal, action=exc.action, outcome="denied", target_type=exc.target_type,
                    target_id=exc.target_id, branch_id=exc.branch_id, reason=exc.message, meta=meta)


__all__ = [
    "Principal", "LoginResult", "SESSION_COOKIE", "CSRF_COOKIE", "authenticate_session", "check_csrf",
    "login", "logout", "hash_password", "verify_password", "validate_new_password", "revoke_user_sessions",
    "record_login_failure", "audit_denied", "load_principal", "utcnow", "sha256", "_LoginFailed",
    "demo_login", "demo_accounts", "open_session",
]
