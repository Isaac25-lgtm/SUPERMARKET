"""Idempotent database bootstrap and deployment migration job.

    python -m motion_api.bootstrap            # migrate + activate/update Tryton modules + base setup
    python -m motion_api.bootstrap --seed-demo  # additionally load clearly-labelled TEST DATA

Run the migration job before starting client instances. Each stage detects
previous work.
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
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

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
    admin_url = urlsplit(target.url)._replace(path="/postgres").geturl()
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=30) as conn:
        try:
            conn.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(target.name)))
        except psycopg.errors.DuplicateDatabase:
            return
    logger.info("created database %s", target.name)


def pin_search_path(settings: Settings) -> None:
    """Tryton tables must live in ``public`` whatever the role is called.

    PostgreSQL's default search_path is ``"$user", public``: with a role named
    like a schema (e.g. ``motion``) Tryton would create its tables there.
    Pinning the database default makes placement environment-independent.
    """
    with psycopg.connect(settings.database.url, autocommit=True, connect_timeout=30) as conn:
        conn.execute(sql.SQL("ALTER DATABASE {} SET search_path = public").format(sql.Identifier(settings.database.name)))


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
    if (args.seed_demo or args.seed_fixtures) and not settings.demo_mode:
        raise RuntimeError("Synthetic data requires demo mode.")
    logger.info("bootstrap target host=%s db=%s env=%s", settings.database.host, settings.database.name, settings.env)
    ensure_database(settings)
    from . import setup_base

    with psycopg.connect(settings.database.url, autocommit=True, connect_timeout=30) as guard:
        guard.execute("SELECT pg_advisory_lock(727003)")
        applied = run_migrations(settings)
        logger.info("platform migrations applied: %s", applied or "none")
        state = init_tryton(settings, force_update=args.force_update)
        logger.info("tryton modules: %s", state)
        setup_base.ensure(settings)
    if args.seed_demo or args.seed_fixtures:
        from . import seed

        seed.run(settings, activity=args.seed_demo)
    logger.info("bootstrap complete")
    return 0


def verify_schema() -> None:
    """Check deployment compatibility without schema owner privileges."""
    applied = {row["version"]: row["checksum"] for row in tryton.sql(
        "SELECT version, checksum FROM motion.schema_migration"
    )}
    for file in MIGRATIONS.glob("*.sql"):
        expected = hashlib.sha256(file.read_text(encoding="utf-8").encode()).hexdigest()
        if applied.get(file.stem) != expected:
            raise RuntimeError(f"Run the release migration job for {file.stem} before starting the API.")
    current = tryton.sql_one("SELECT value FROM motion.app_setting WHERE key='tryton_fingerprint'")
    if not current or current["value"] != _module_fingerprint():
        raise RuntimeError("Run the release migration job to update the Tryton modules.")


if __name__ == "__main__":
    sys.exit(main())
