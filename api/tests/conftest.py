"""Integration test harness: real PostgreSQL + real Tryton engine + real HTTP API.

* A fixture template database (schema, Tryton modules, base setup, TEST users,
  catalog and opening stock — no generated history) is built once and cached,
  keyed by a fingerprint of the code that shapes it.
* Every test session clones a fresh ``motion_test`` from the template
  (``CREATE DATABASE ... TEMPLATE``), so runs are independent and fast.

Requires MOTION_TEST_DATABASE_URL pointing at a *disposable* server (see
infra/scripts/dev-db.sh). The harness refuses non-local hosts.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "api"
sys.path.insert(0, str(API))

from motion_api.settings import _load_env_file  # noqa: E402

_load_env_file(Path(os.environ.get("MOTION_ENV_FILE", ROOT / ".env.dev")))
TEST_URL = os.environ.get("MOTION_TEST_DATABASE_URL")
if not TEST_URL:
    raise RuntimeError("MOTION_TEST_DATABASE_URL is required (run infra/scripts/dev-db.sh init)")
test_target = urlsplit(TEST_URL)
if (test_target.scheme not in {"postgres", "postgresql"} or test_target.hostname not in {"127.0.0.1", "::1"}
        or test_target.path != "/motion_test" or test_target.query or test_target.fragment or not test_target.port):
    raise RuntimeError("Use an explicit disposable loopback PostgreSQL URL pointing to motion_test.")
TEMPLATE_URL = test_target._replace(path="/motion_test_tpl").geturl()
ADMIN_URL = test_target._replace(path="/postgres").geturl()


def _fingerprint() -> str:
    h = hashlib.sha256()
    files = sorted((API / "motion_api" / "migrations").glob("*.sql")) + [
        API / "motion_api" / n for n in ("setup_base.py", "seed.py", "creds.py", "permissions.py", "bootstrap.py")]
    files += sorted((ROOT / "tryton-modules" / "motion_branch" / "motion_branch").rglob("*.*"))
    files += [API / "motion_api" / "config" / "chain.json"]
    for f in files:
        if f.is_file() and f.suffix in {".py", ".sql", ".xml", ".cfg", ".json"}:
            h.update(f.read_bytes())
    return h.hexdigest()[:20]


def _template_fp() -> str | None:
    try:
        with psycopg.connect(TEMPLATE_URL, connect_timeout=5) as c:
            row = c.execute("SELECT value->>'fp' FROM motion.app_setting WHERE key='test_template'").fetchone()
            return row[0] if row else None
    except psycopg.Error:
        return None


def _build_template(fp: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute('DROP DATABASE IF EXISTS "motion_test_tpl" WITH (FORCE)')
    env = {**os.environ, "DATABASE_URL": TEMPLATE_URL, "PYTHONPATH": str(API), "MOTION_ENV_FILE": ""}
    subprocess.run([sys.executable, "-m", "motion_api.bootstrap", "--seed-fixtures"], env=env, check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL)
    with psycopg.connect(TEMPLATE_URL) as c:
        c.execute("INSERT INTO motion.app_setting (key, value) VALUES ('test_template', jsonb_build_object('fp', %s::text))",
                  (fp,))
        c.commit()


def _clone() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute('DROP DATABASE IF EXISTS "motion_test" WITH (FORCE)')
        c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s', ("motion_test_tpl",))
        c.execute('CREATE DATABASE "motion_test" TEMPLATE "motion_test_tpl"')


fp = _fingerprint()
if _template_fp() != fp:
    print(f"[tests] building fixture template ({fp}) — first run takes ~3 minutes", file=sys.stderr)
    _build_template(fp)
_clone()
os.environ["DATABASE_URL"] = TEST_URL
os.environ["MOTION_ENV"] = "test"
os.environ.pop("MOTION_AUTO_BOOTSTRAP", None)

from fastapi.testclient import TestClient  # noqa: E402
from motion_api import tryton as t  # noqa: E402
from motion_api.creds import demo_password  # noqa: E402
from motion_api.main import STATE, app  # noqa: E402

t.init_pool()
STATE.update(ready=True, phase="ready")  # TestClient without a context manager skips startup events


class Api:
    """Logged-in HTTP session (cookies + CSRF header handled)."""

    def __init__(self, username: str, password: str | None = None):
        self.username = username
        self.c = TestClient(app, raise_server_exceptions=True)
        r = self.c.post("/api/auth/login", json={"username": username, "password": password or demo_password(username)})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        self.me = self.c.get("/api/auth/me").json()

    def get(self, path, **params):
        return self.c.get(path, params=params)

    def post(self, path, body=None):
        return self.c.post(path, json=body or {}, headers={"X-CSRF-Token": self.csrf})

    def put(self, path, body=None):
        return self.c.put(path, json=body or {}, headers={"X-CSRF-Token": self.csrf})

    def patch(self, path, body=None):
        return self.c.patch(path, json=body or {}, headers={"X-CSRF-Token": self.csrf})


@pytest.fixture(scope="session")
def login():
    cache: dict[str, Api] = {}

    def _login(username: str, fresh: bool = False) -> Api:
        # Re-login when a test revoked the cached session (suspension, access change).
        if fresh or username not in cache or cache[username].get("/api/auth/me").status_code == 401:
            cache[username] = Api(username)
        return cache[username]
    return _login


def q(sql: str, params=None):
    return t.run(lambda: t.sql(sql, params), readonly=True)


def q1(sql: str, params=None):
    rows = q(sql, params)
    return rows[0] if rows else None


@pytest.fixture(scope="session")
def ids():
    branches = {r["code"]: r["id"] for r in q("SELECT id, code FROM motion_branch")}
    registers = {r["name"]: r["id"] for r in q("SELECT id, name FROM sale_point")}
    products = {r["name"]: r["id"] for r in q(
        "SELECT pp.id, pt.name FROM product_product pp JOIN product_template pt ON pt.id=pp.template")}
    storage = {r["code"]: r["storage_location"] for r in q(
        "SELECT b.code, w.storage_location FROM motion_branch b JOIN stock_location w ON w.id=b.warehouse")}
    users = {r["username"]: r["id"] for r in q("SELECT id, username FROM motion.app_user")}
    return {"branch": branches, "register": registers, "product": products, "storage": storage, "user": users}


def pid(ids, prefix: str) -> int:
    return next(v for k, v in ids["product"].items() if k.startswith(prefix))


def on_hand(location_id: int, product_id: int) -> Decimal:
    from motion_api.services import common
    return t.run(lambda: common.stock_on_hand([location_id], [product_id]).get((location_id, product_id), Decimal(0)),
                 readonly=True)


def account_balance(code: str, branch_id: int | None = None) -> Decimal:
    row = q1("""SELECT COALESCE(sum(l.debit - l.credit), 0) AS b FROM account_move_line l JOIN account_move m ON m.id=l.move
                JOIN account_account a ON a.id=l.account WHERE m.state='posted' AND a.code=%s
                AND (%s::int IS NULL OR m.branch=%s)""", (code, branch_id, branch_id))
    return Decimal(row["b"])


@pytest.fixture()
def open_shift(login, ids):
    """Ensure a cashier has an open shift on a register; returns (api, register_id, shift)."""
    def _open(username: str, register: str, float_amount: str = "100000"):
        api = login(username)
        cur = api.get("/api/pos/shift").json()["shift"]
        if cur:
            return api, ids["register"][register], cur
        r = api.post("/api/pos/shifts", {"register_id": ids["register"][register], "opening_float": float_amount})
        assert r.status_code == 201, r.text
        return api, ids["register"][register], r.json()
    return _open


def new_key() -> str:
    return str(uuid.uuid4())
