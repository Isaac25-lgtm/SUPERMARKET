#!/usr/bin/env bash
# Drop and rebuild the LOCAL dev database (never Neon): schema + Tryton modules + base setup + TEST DATA.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/Scripts/python}"; [ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
export MOTION_ENV_FILE="${MOTION_ENV_FILE:-.env.dev}" PYTHONPATH="$ROOT/api"
"$PY" - <<'PYEOF'
import os, psycopg
from motion_api.settings import get_settings
s = get_settings()
assert "127.0.0.1" in s.database.host or "localhost" in s.database.host, "refusing to reset a non-local database"
admin = s.database.url.rsplit("/", 1)[0] + "/postgres"
with psycopg.connect(admin, autocommit=True) as c:
    c.execute(f'DROP DATABASE IF EXISTS "{s.database.name}" WITH (FORCE)')
print("dropped", s.database.name)
PYEOF
"$PY" -m motion_api.bootstrap --seed-demo
