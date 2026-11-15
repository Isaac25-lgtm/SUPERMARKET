"""One idempotency contract for money-moving commands (Codex H2).

Usage inside a unit of work::

    replay = idempotency.claim("pos.sale", actor, key, request)
    if replay:
        return receipt(actor, int(replay))
    ... do the work ...
    idempotency.complete("pos.sale", actor, key, result_type="pos_sale", result_id=row["id"])

``claim`` runs *before* anything is touched (stock guards, line counters,
approvals). Same key + same request returns the earlier result; same key + a
different request is refused. The operation row is inserted by ``complete`` in
the same database transaction as the business result, so it exists exactly when
the result does. Two simultaneous requests with one key: the second blocks on the
primary key until the first commits, fails serialization, is retried by the
engine with a fresh snapshot and then replays the first result.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from .. import tryton as t
from ..errors import Conflict, Invalid
from ..security import Principal


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items()) if v is not None}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    return str(value)


def fingerprint(request: dict) -> str:
    return hashlib.sha256(json.dumps(_canonical(request), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _check_key(key: str | None) -> str:
    key = (key or "").strip()
    if not 8 <= len(key) <= 120:
        raise Invalid("Missing or invalid idempotency key.")
    return key


def claim(operation: str, actor: Principal, key: str | None, request: dict) -> str | None:
    """Return the earlier result id for a replay, None for a fresh operation."""
    key = _check_key(key)
    row = t.sql_one("""SELECT fingerprint, result_id FROM motion.operation
                       WHERE operation=%s AND actor_id=%s AND key=%s""", (operation, actor.user_id, key))
    if not row:
        return None
    if row["fingerprint"] != fingerprint(request):
        raise Conflict("This request key was already used for a different transaction. Start a new one.",
                       details={"reason": "idempotency_key_reused"})
    return row["result_id"]


def complete(operation: str, actor: Principal, key: str, request: dict, *, result_type: str, result_id: Any) -> None:
    t.execute("""INSERT INTO motion.operation (operation, actor_id, key, fingerprint, result_type, result_id)
                 VALUES (%s,%s,%s,%s,%s,%s)""",
              (operation, actor.user_id, key.strip(), fingerprint(request), result_type, str(result_id)))


def internal_key(operation: str, actor: Principal, key: str) -> str:
    """Globally unique key for tables that keep their own unique idempotency column."""
    return f"{operation}:{actor.user_id}:{key.strip()}"
