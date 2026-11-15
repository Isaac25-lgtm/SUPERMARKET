"""Runtime configuration.

The only required production setting is ``DATABASE_URL`` (Neon connection
string, set as a Render secret).  Everything else has safe defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DatabaseTarget:
    url: str
    name: str
    tryton_uri: str
    host: str


def parse_database_url(url: str, prefer_direct: bool = True) -> DatabaseTarget:
    """Split a libpq URL into Tryton's (server URI, database name) pair.

    Tryton takes the database name separately and rejects a path in its URI.
    Query parameters (``sslmode=require``, ``channel_binding``) are preserved
    and passed to libpq unchanged.  For Neon, the ``-pooler`` host is swapped
    for the direct endpoint when ``prefer_direct`` is set, because PgBouncer
    transaction pooling is a poor fit for an ORM that keeps its own pool.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"postgresql", "postgres"}:
        raise ValueError("DATABASE_URL must be a postgresql:// URL")
    name = parts.path.lstrip("/") or "postgres"
    netloc = parts.netloc
    host = parts.hostname or ""
    if prefer_direct and "-pooler." in host:
        direct = host.replace("-pooler.", ".", 1)
        netloc = netloc.replace(host, direct, 1)
        host = direct
    tryton_uri = urlunsplit(("postgresql", netloc, "/", parts.query, ""))
    full = urlunsplit(("postgresql", netloc, "/" + name, parts.query, ""))
    return DatabaseTarget(url=full, name=name, tryton_uri=tryton_uri, host=host)


@dataclass(frozen=True)
class Settings:
    env: str
    database: DatabaseTarget
    secret_key: str
    session_ttl_minutes: int
    session_idle_minutes: int
    cookie_secure: bool
    demo_mode: bool
    brand_name: str
    company_name: str
    timezone: str
    currency: str
    web_dist: Path
    tryton_maxconn: int
    ai_llm_enabled: bool
    login_max_failures: int
    login_lock_minutes: int
    offline_grant_hours: int
    open_demo: bool = False
    allowed_hosts: list[str] = field(default_factory=list)

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env_file = os.environ.get("MOTION_ENV_FILE")
    if env_file:
        _load_env_file(Path(env_file))
    env = os.environ.get("MOTION_ENV", "development")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. On Render set it as a secret env var; "
            "locally run infra/scripts/dev-db.sh init and use MOTION_ENV_FILE=.env.dev")
    secret = os.environ.get("MOTION_SECRET_KEY", "")
    if env == "production" and len(secret) < 32:
        raise RuntimeError("MOTION_SECRET_KEY (>=32 chars) is required in production")
    if not secret:
        secret = "dev-only-insecure-secret-key-change-me-0000"
    return Settings(
        env=env,
        database=parse_database_url(url, _bool("MOTION_DB_PREFER_DIRECT", True)),
        secret_key=secret,
        session_ttl_minutes=int(os.environ.get("MOTION_SESSION_TTL_MINUTES", "720")),
        session_idle_minutes=int(os.environ.get("MOTION_SESSION_IDLE_MINUTES", "120")),
        cookie_secure=_bool("MOTION_COOKIE_SECURE", env == "production"),
        demo_mode=_bool("MOTION_DEMO_MODE", True),
        brand_name=os.environ.get("MOTION_BRAND_NAME", "FreshWay Supermarkets"),
        company_name=os.environ.get("MOTION_COMPANY_NAME", "FreshWay Supermarkets Ltd (TEST)"),
        timezone=os.environ.get("MOTION_TIMEZONE", "Africa/Kampala"),
        currency=os.environ.get("MOTION_CURRENCY", "UGX"),
        web_dist=Path(os.environ.get("MOTION_WEB_DIST", str(ROOT / "web" / "dist"))),
        tryton_maxconn=int(os.environ.get("MOTION_DB_MAXCONN", "8")),
        ai_llm_enabled=_bool("MOTION_AI_LLM_ENABLED", False),
        login_max_failures=int(os.environ.get("MOTION_LOGIN_MAX_FAILURES", "5")),
        login_lock_minutes=int(os.environ.get("MOTION_LOGIN_LOCK_MINUTES", "15")),
        offline_grant_hours=int(os.environ.get("MOTION_OFFLINE_GRANT_HOURS", "12")),
        # Password-less "continue as" sign-in for TEST DATA demos only. Refused unless
        # demo mode (TEST DATA banner) is also on, so it cannot guard real data.
        open_demo=_bool("MOTION_OPEN_DEMO", False) and _bool("MOTION_DEMO_MODE", True),
    )
