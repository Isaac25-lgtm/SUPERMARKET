"""Durable local store (SQLite, WAL) for the branch edge.

Concurrency: several tills on the LAN call the edge at once.  Every sale runs
in a ``BEGIN IMMEDIATE`` transaction, which takes SQLite's single write lock,
so two cashiers selling the last unit are serialised: the second sees the
first's reservation and is refused (when the policy disallows negative stock).

Durability: ``synchronous=FULL`` + WAL — a sale acknowledged to the till has
been fsync'ed to disk and survives a power cut of the edge PC.

Immutability: outbox payloads can never be changed after insertion (trigger);
only delivery-status columns move forward.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY, data TEXT NOT NULL, snapshot_on_hand TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS grants (
    username TEXT PRIMARY KEY, full_name TEXT NOT NULL, roles TEXT NOT NULL,
    verifier TEXT NOT NULL, offline_until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, register_id INTEGER NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_failure (
    username TEXT NOT NULL, client_ip TEXT NOT NULL, attempted_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS login_failure_at ON login_failure(attempted_at);
CREATE TABLE IF NOT EXISTS receipt_seq (register_id INTEGER PRIMARY KEY, next INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    register_id INTEGER NOT NULL,
    cashier TEXT NOT NULL,
    local_receipt_no TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'acked', 'rejected')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    central_number TEXT,
    ack TEXT,
    acked_at TEXT,
    in_snapshot INTEGER NOT NULL DEFAULT 0,
    request_hash TEXT);
CREATE INDEX IF NOT EXISTS outbox_status ON outbox(status, created_at);
CREATE TRIGGER IF NOT EXISTS outbox_payload_immutable BEFORE UPDATE OF payload, event_id, created_at, cashier,
    register_id, local_receipt_no ON outbox
BEGIN SELECT RAISE(ABORT, 'outbox events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS outbox_no_delete BEFORE DELETE ON outbox
BEGIN SELECT RAISE(ABORT, 'outbox events cannot be deleted'); END;
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            if os.name == "posix":
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
        self._local = threading.local()
        with self.connect() as c:
            c.executescript(SCHEMA)
        with self.write() as c:
            columns = {row["name"] for row in c.execute("PRAGMA table_info(outbox)")}
            if "in_snapshot" not in columns:
                c.execute("ALTER TABLE outbox ADD COLUMN in_snapshot INTEGER NOT NULL DEFAULT 0")
            if "request_hash" not in columns:
                c.execute("ALTER TABLE outbox ADD COLUMN request_hash TEXT")
            c.execute("""CREATE TRIGGER IF NOT EXISTS outbox_request_immutable BEFORE UPDATE OF request_hash ON outbox
                         BEGIN SELECT RAISE(ABORT, 'outbox request identity is immutable'); END""")
            c.execute("""CREATE INDEX IF NOT EXISTS outbox_unreflected ON outbox(event_id)
                         WHERE in_snapshot=0""")

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def connect(self):
        yield self._conn()

    @contextmanager
    def write(self):
        """Exclusive write transaction (serialises concurrent tills)."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def get_meta(self, key: str, default=None):
        with self.connect() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else default

    def set_meta(self, conn: sqlite3.Connection, key: str, value) -> None:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, default=str)))
