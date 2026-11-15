#!/usr/bin/env bash
# Disposable PostgreSQL cluster for LOCAL DEVELOPMENT AND AUTOMATED TESTS ONLY.
# Production uses Neon via DATABASE_URL on Render. This never touches any other
# PostgreSQL server on the machine: it runs its own data dir on its own port,
# bound to 127.0.0.1, with a generated password stored in .env.dev (git-ignored).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PGBIN="${PGBIN:-}"
if [ -z "$PGBIN" ]; then
  for c in "/c/Program Files/PostgreSQL/18/bin" "/c/Program Files/PostgreSQL/17/bin" "/usr/lib/postgresql/18/bin" "/usr/lib/postgresql/17/bin" "/usr/local/bin" "/usr/bin"; do
    [ -x "$c/initdb" ] || [ -x "$c/initdb.exe" ] && { PGBIN="$c"; break; }
  done
fi
[ -n "$PGBIN" ] || { echo "initdb not found; set PGBIN"; exit 1; }
DATA="$ROOT/infra/.pgdata"; LOG="$ROOT/infra/.pglog"; PORT="${MOTION_DEV_PGPORT:-55432}"
ENVF="$ROOT/.env.dev"
cmd="${1:-status}"
case "$cmd" in
  init)
    [ -d "$DATA" ] && { echo "already initialised at $DATA"; exit 0; }
    PW="$(python -c 'import secrets;print(secrets.token_urlsafe(24))')"
    PWF="$(mktemp)"; printf '%s' "$PW" > "$PWF"
    "$PGBIN/initdb" -D "$DATA" -U motion --pwfile="$PWF" -A scram-sha-256 -E UTF8 --locale=C >/dev/null
    rm -f "$PWF"
    cat >> "$DATA/postgresql.conf" <<CONF
listen_addresses = '127.0.0.1'
port = $PORT
max_connections = 60
shared_buffers = 64MB
fsync = on
CONF
    cat > "$ENVF" <<ENV
# LOCAL DEV/TEST ONLY — git-ignored. Disposable cluster on 127.0.0.1:$PORT
DATABASE_URL=postgresql://motion:$PW@127.0.0.1:$PORT/motion_dev
MOTION_TEST_DATABASE_URL=postgresql://motion:$PW@127.0.0.1:$PORT/motion_test
ENV
    echo "initialised; credentials written to .env.dev" ;;
  start) mkdir -p "$LOG"; "$PGBIN/pg_ctl" -D "$DATA" -l "$LOG/postgres.log" -w start >/dev/null && echo "started on 127.0.0.1:$PORT" ;;
  stop) "$PGBIN/pg_ctl" -D "$DATA" -w stop -m fast ;;
  status) "$PGBIN/pg_ctl" -D "$DATA" status || true ;;
  destroy)
    "$PGBIN/pg_ctl" -D "$DATA" -w stop -m fast 2>/dev/null || true
    rm -rf "$DATA" "$LOG" "$ENVF"; echo "destroyed local dev cluster" ;;
  *) echo "usage: dev-db.sh init|start|stop|status|destroy"; exit 2 ;;
esac
