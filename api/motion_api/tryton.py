"""In-process gateway to the Tryton 8.0 engine.

Every unit of work runs inside exactly one Tryton transaction (one PostgreSQL
transaction, REPEATABLE READ).  The platform's own tables (schema ``motion``)
are written through the *same* connection, so a POS sale, its stock/ledger
postings, its idempotency record and its audit event commit or roll back
together.

Concurrency: Tryton runs at REPEATABLE READ.  Conflicting writes raise a
serialization failure; :func:`run` retries the whole unit of work with a fresh
snapshot (the same strategy Tryton's own RPC dispatcher uses).

Authorization is NOT delegated to Tryton here: callers must have passed the
platform's role/branch checks (see ``security.py``) before calling :func:`run`.
The ``user`` argument is the mapped Tryton ``res.user`` of the human actor so
Tryton's own ``create_uid``/``write_uid`` columns attribute records correctly.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

os.environ.setdefault("TRYTOND_APPNAME", "motion-api")
os.environ.setdefault("TRYTOND_TZ", "UTC")
if os.name == "nt":  # Windows dev boxes: Tryton insists on UTC; offset is what matters.
    os.environ.setdefault("TZ", "UTC0")
import warnings  # noqa: E402

warnings.filterwarnings("ignore", message="Timezone must be set to UTC")

from trytond.config import config  # noqa: E402

from .settings import Settings  # noqa: E402

logger = logging.getLogger(__name__)
T = TypeVar("T")

MODULES = [
    "company", "currency", "country", "party", "product", "stock",
    "account", "account_product", "account_invoice", "account_invoice_stock",
    "account_stock_continental", "sale", "purchase", "sale_point",
    "motion_branch",
]

_lock = threading.Lock()
_db_name: str | None = None
_pool_ready = False
# Lock requirements learned per operation (see run()): avoids paying Tryton's
# lock-escalation restart on every call of e.g. checkout (strict sequences).
_learned_extras: dict[str, dict[str, Any]] = {}


class EngineError(Exception):
    """A business rule raised by Tryton (UserError/UserWarning)."""

    def __init__(self, message: str, *, kind: str = "user_error"):
        super().__init__(message)
        self.kind = kind


def configure(settings: Settings) -> None:
    """Point Tryton's global config at the target database (no I/O)."""
    global _db_name
    config.set("database", "uri", settings.database.tryton_uri)
    config.set("database", "maxconn", str(settings.tryton_maxconn))
    config.set("database", "minconn", "1")
    config.set("database", "language", "en")
    # Strict (gap-free) sequences take a table lock with NOWAIT; concurrent tills
    # retry briefly instead of failing (sum of back-off sleeps ~8s at 30).
    config.set("database", "retry", os.environ.get("MOTION_DB_LOCK_RETRY", "30"))
    # Filestore is not used for business data (Render disks are ephemeral).
    filestore = os.environ.get("MOTION_FILESTORE", os.path.join(os.getcwd(), "var", "filestore"))
    config.set("database", "path", filestore)
    # Poll-based cache invalidation: works through Neon's PgBouncer (no LISTEN).
    config.set("cache", "clean_timeout", os.environ.get("MOTION_CACHE_CLEAN_TIMEOUT", "60"))
    _db_name = settings.database.name


def db_name() -> str:
    if not _db_name:
        raise RuntimeError("tryton.configure() has not been called")
    return _db_name


def init_pool() -> None:
    """Load the Tryton model pool for the configured database (once)."""
    global _pool_ready
    with _lock:
        if _pool_ready:
            return
        from trytond.pool import Pool

        Pool.start()
        Pool(db_name()).init()
        _pool_ready = True
        logger.info("tryton pool ready for %s", db_name())
    _load_default_context()


_default_context: dict[str, Any] = {"language": "en"}


def _load_default_context() -> None:
    """Company context for every unit of work (Tryton multi-company fields need it)."""
    from trytond.transaction import Transaction

    if "company" in _default_context:
        return
    with Transaction().start(db_name(), 0, readonly=True) as tr:
        cur = tr.connection.cursor()
        cur.execute("SELECT count(*) FROM pg_class WHERE relname = 'company_company' AND relkind = 'r'")
        if not cur.fetchone()[0]:
            return  # fresh database, before base setup
        cur.execute("SELECT id FROM company_company ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if row:
            _default_context["company"] = row[0]


def _is_retryable(exc: BaseException) -> bool:
    from trytond import backend

    return isinstance(exc, backend.DatabaseOperationalError)


def run(
    fn: Callable[[], T],
    *,
    user: int = 0,
    readonly: bool = False,
    context: dict[str, Any] | None = None,
    retries: int = 6,
    op_key: str | None = None,
) -> T:
    """Execute ``fn`` inside a Tryton transaction, retrying on serialization.

    ``op_key`` names the operation so table/record locks Tryton asked for on a
    previous call are taken up-front next time (no restart).
    """
    import copy

    from trytond.exceptions import UserError, UserWarning
    from trytond.transaction import Transaction, TransactionError

    init_pool()
    attempt = 0
    extras: dict[str, Any] = copy.deepcopy(_learned_extras.get(op_key, {})) if op_key else {}
    while True:
        try:
            if "company" not in _default_context:
                _load_default_context()
            ctx = {**_default_context, **(context or {})}
            with Transaction().start(db_name(), user, readonly=readonly, context=ctx, **extras):
                return fn()
        except TransactionError as exc:
            # Tryton's lock-escalation protocol: restart with the table/records
            # locked from the beginning (same as trytond's RPC dispatcher).
            exc.fix(extras)
            if op_key:
                learned = _learned_extras.setdefault(op_key, {})
                for table in extras.get("_lock_tables", []):
                    if table not in learned.setdefault("_lock_tables", []):
                        learned["_lock_tables"].append(table)
                for table, ids in extras.get("_lock_records", {}).items():
                    known = learned.setdefault("_lock_records", {}).setdefault(table, [])
                    known.extend(i for i in ids if i not in known)  # Tryton extends lists
            attempt += 1
            if attempt > retries + 4:
                raise
            continue
        except (UserError, UserWarning) as exc:
            message = getattr(exc, "message", None) or str(exc)
            raise EngineError(message, kind=type(exc).__name__) from exc
        except Exception as exc:  # noqa: BLE001 - classify then re-raise
            if _is_retryable(exc) and attempt < retries:
                attempt += 1
                time.sleep(min(0.4, 0.02 * (2 ** attempt)) * (0.5 + random.random()))
                logger.info("retrying transaction after %s (attempt %d)", type(exc).__name__, attempt)
                continue
            raise


# --- helpers usable inside run() -------------------------------------------------

def pool():
    from trytond.pool import Pool

    return Pool()


def model(name: str):
    return pool().get(name)


def transaction():
    from trytond.transaction import Transaction

    return Transaction()


def cursor():
    return transaction().connection.cursor()


def sql(query: str, params: Sequence[Any] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run raw SQL on the current transaction's connection; return dict rows.

    Used for the platform's own ``motion`` schema and for read-only reporting
    aggregates.  Never used to write Tryton business tables.
    """
    cur = cursor()
    cur.execute(query, params)
    if cur.description is None:
        return []
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_one(query: str, params: Sequence[Any] | dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = sql(query, params)
    return rows[0] if rows else None


def execute(query: str, params: Sequence[Any] | dict[str, Any] | None = None) -> int:
    cur = cursor()
    cur.execute(query, params)
    return cur.rowcount


@contextmanager
def set_context(**values: Any):
    with transaction().set_context(**values):
        yield


def ids(records: Iterable[Any]) -> list[int]:
    return [r.id for r in records]
