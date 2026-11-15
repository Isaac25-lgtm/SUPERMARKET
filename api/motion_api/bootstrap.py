"""Idempotent database bootstrap.

    python -m motion_api.bootstrap            # migrate + activate/update Tryton modules + base setup
    python -m motion_api.bootstrap --seed-demo  # additionally load clearly-labelled TEST DATA

Safe to run on every deploy (Render start command): each stage detects what
is already done.  It never drops data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from importlib import metadata
from pathlib import Path

import psycopg

from . import tryton
from .settings import Settings, get_settings

logger = logging.getLogger("motion.bootstrap")
MIGRATIONS = Path(__file__).with_name("migrations")


def ensure_database(settings: Settings) -> None:
    """Create the database when it does not exist (local dev/test clusters).

    On Neon the database (``neondb``) already exists and this is a no-op.
    """
    target = settings.database
    try:
        with psycopg.connect(target.url, connect_timeout=30) as conn:
            conn.execute("SELECT 1")
        return
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    admin_url = target.url.rsplit("/", 1)[0] + "/postgres"
    if "?" in target.url:
        admin_url += "?" + target.url.split("?", 1)[1]
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=30) as conn:
        conn.execute(f'CREATE DATABASE "{target.name}" ENCODING \'UTF8\' TEMPLATE template0')
    logger.info("created database %s", target.name)


def pin_search_path(settings: Settings) -> None:
    """Tryton tables must live in ``public`` whatever the role is called.

    PostgreSQL's default search_path is ``"$user", public``: with a role named
    like a schema (e.g. ``motion``) Tryton would create its tables there.
    Pinning the database default makes placement environment-independent.
    """
    with psycopg.connect(settings.database.url, autocommit=True, connect_timeout=30) as conn:
        conn.execute(f'ALTER DATABASE "{settings.database.name}" SET search_path = public')


def run_migrations(settings: Settings) -> list[str]:
    applied: list[str] = []
    pin_search_path(settings)
    with psycopg.connect(settings.database.url, connect_timeout=30) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS motion")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS motion.schema_migration ("
            " version text PRIMARY KEY, checksum text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())")
        conn.execute("SELECT pg_advisory_xact_lock(727001)")
        done = {r[0]: r[1] for r in conn.execute(
            "SELECT version, checksum FROM motion.schema_migration")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            body = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(body.encode()).hexdigest()
            if path.stem in done:
                if done[path.stem] != checksum:
                    raise RuntimeError(
                        f"migration {path.name} changed after being applied; add a new migration instead")
                continue
            conn.execute(body)
            conn.execute(
                "INSERT INTO motion.schema_migration (version, checksum) VALUES (%s, %s)",
                (path.stem, checksum))
            applied.append(path.stem)
        conn.commit()
    return applied


def _module_fingerprint() -> str:
    parts = []
    for dist in ("trytond", *(f"trytond_{m}" for m in tryton.MODULES if m != "motion_branch")):
        try:
            parts.append(f"{dist}={metadata.version(dist)}")
        except metadata.PackageNotFoundError:
            parts.append(f"{dist}=missing")
    module_dir = Path(__import__("motion_branch").__file__).parent
    digest = hashlib.sha256()
    for f in sorted(module_dir.rglob("*")):
        if f.is_file() and f.suffix in {".py", ".xml", ".cfg"}:
            digest.update(f.name.encode())
            digest.update(f.read_bytes())
    parts.append("motion_branch=" + digest.hexdigest()[:16])
    return ";".join(parts)


def _get_setting(settings: Settings, key: str):
    with psycopg.connect(settings.database.url) as conn:
        row = conn.execute("SELECT value FROM motion.app_setting WHERE key=%s", (key,)).fetchone()
        return row[0] if row else None


def _put_setting(settings: Settings, key: str, value) -> None:
    with psycopg.connect(settings.database.url) as conn:
        conn.execute(
            "INSERT INTO motion.app_setting (key, value) VALUES (%s, %s::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (key, json.dumps(value)))
        conn.commit()


def init_tryton(settings: Settings, force_update: bool = False) -> str:
    tryton.configure(settings)  # must precede any trytond.backend import
    from trytond import backend
    from trytond.pool import Pool

    name = settings.database.name
    database = backend.Database(name)
    database.connect()
    fresh = False
    if not database.test():
        logger.info("initialising Tryton schema in %s", name)
        database.init()
        fresh = True
    fingerprint = _module_fingerprint()
    previous = None if fresh else _get_setting(settings, "tryton_fingerprint")
    if fresh or force_update or previous != fingerprint:
        started = time.monotonic()
        Pool.start()
        Pool(name).init(update=["ir", "res", *tryton.MODULES], lang=["en"], activatedeps=True)
        _put_setting(settings, "tryton_fingerprint", fingerprint)
        logger.info("tryton modules activated/updated in %.0fs", time.monotonic() - started)
        return "updated"
    return "unchanged"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-demo", action="store_true", help="load TEST DATA (idempotent)")
    parser.add_argument("--seed-fixtures", action="store_true", help="TEST users/catalog/stock only (automated tests)")
    parser.add_argument("--force-update", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    logger.info("bootstrap target host=%s db=%s env=%s", settings.database.host, settings.database.name, settings.env)
    ensure_database(settings)
    applied = run_migrations(settings)
    logger.info("platform migrations applied: %s", applied or "none")
    state = init_tryton(settings, force_update=args.force_update)
    logger.info("tryton modules: %s", state)
    from . import setup_base

    setup_base.ensure(settings)
    if args.seed_demo or args.seed_fixtures:
        from . import seed

        seed.run(settings, activity=args.seed_demo)
    logger.info("bootstrap complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
