"""Append-only business audit trail (motion.audit_event).

``record`` writes inside the caller's transaction so the audit row commits
atomically with the business change it describes.  ``record_detached`` opens
its own transaction and is used for events that must survive a rollback
(denied access, failed logins).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from . import tryton as t

SENSITIVE_KEYS = {"password", "password_hash", "token", "pin", "pos_pin", "pos_pin_hash", "csrf", "secret"}


@dataclass
class RequestMeta:
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    session_ref: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in SENSITIVE_KEYS else _clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_clean(value), default=str)


def record(*, actor=None, action: str, outcome: str = "success", target_type: str | None = None,
           target_id: Any = None, branch_id: int | None = None, amount: Decimal | None = None,
           reason: str | None = None, before: Any = None, after: Any = None,
           meta: RequestMeta | None = None) -> None:
    meta = meta or (getattr(actor, "meta", None) if actor else None) or RequestMeta()
    t.execute(
        """INSERT INTO motion.audit_event
           (actor_id, actor_username, actor_roles, branch_id, action, outcome, target_type, target_id,
            amount, reason, before_state, after_state, request_id, ip, user_agent, session_ref)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)""",
        (
            getattr(actor, "user_id", None), getattr(actor, "username", None),
            sorted(getattr(actor, "roles", []) or []), branch_id, action, outcome, target_type,
            None if target_id is None else str(target_id), amount, reason, _dump(before), _dump(after),
            meta.request_id, meta.ip, (meta.user_agent or "")[:300] or None, meta.session_ref,
        ),
    )


def record_detached(**kwargs) -> None:
    """Write an audit event in its own transaction (survives caller rollback)."""
    t.run(lambda: record(**kwargs), user=0)
