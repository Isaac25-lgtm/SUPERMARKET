"""Edge business logic: snapshot, offline login, offline sale, sync."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import secrets
import uuid
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .store import Store

logger = logging.getLogger("motion.edge")
_hasher = PasswordHasher()
SESSION_HOURS = 10


class EdgeError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def money(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def line_amount(q, unit) -> Decimal:
    """Same rule as the central system and Tryton: half-even to whole shillings."""
    return (Decimal(str(q)) * Decimal(str(unit))).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)


def qty(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.001"))


def sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


# ---------------------------------------------------------------- snapshot

def apply_snapshot(store: Store, snap: dict) -> None:
    with store.write() as c:
        c.execute("DELETE FROM items")
        for it in snap["items"]:
            c.execute("INSERT INTO items (id, data, snapshot_on_hand) VALUES (?,?,?)",
                      (it["id"], json.dumps(it, default=str), str(it["on_hand"])))
        c.execute("DELETE FROM grants")
        for g in snap["grants"]:
            c.execute("INSERT INTO grants (username, full_name, roles, verifier, offline_until) VALUES (?,?,?,?,?)",
                      (g["username"].lower(), g["full_name"], json.dumps(g["roles"]), g["verifier"], str(g["offline_until"])))
        # Sessions of users who no longer have a grant end immediately.
        c.execute("DELETE FROM sessions WHERE username NOT IN (SELECT username FROM grants)")
        for key in ("branch", "registers", "policy", "methods", "price_version", "server_time", "brand", "test_data"):
            store.set_meta(c, key, snap.get(key))
        store.set_meta(c, "snapshot_at", now().isoformat())
        # Pending sales made before this snapshot reached central are not in it yet.
        store.set_meta(c, "snapshot_outbox_boundary", now().isoformat())


def available(store: Store, conn, product_id: int) -> Decimal:
    row = conn.execute("SELECT snapshot_on_hand FROM items WHERE id=?", (product_id,)).fetchone()
    if not row:
        return Decimal(0)
    pending = Decimal(0)
    for r in conn.execute("SELECT payload FROM outbox WHERE status='pending'"):
        for ln in json.loads(r["payload"])["lines"]:
            if int(ln["product_id"]) == product_id:
                pending += Decimal(ln["quantity"])
    return qty(Decimal(row["snapshot_on_hand"]) - pending)


# ---------------------------------------------------------------- auth

def login(store: Store, username: str, password: str, register_id: int) -> dict:
    uname = (username or "").strip().lower()
    with store.connect() as c:
        g = c.execute("SELECT * FROM grants WHERE username=?", (uname,)).fetchone()
    try:
        ok = bool(g) and _hasher.verify(g["verifier"], password or "")
    except (VerificationError, InvalidHashError):
        ok = False
    if not ok:
        raise EdgeError("Incorrect username or password (offline sign-in).", 401)
    until = dt.datetime.fromisoformat(g["offline_until"])
    if until <= now():
        raise EdgeError("Offline sign-in has expired for this account; reconnect to the internet to refresh access.", 401)
    regs = {r["id"] for r in (store.get_meta("registers") or [])}
    if register_id not in regs:
        raise EdgeError("Unknown register for this branch.", 400)
    token = secrets.token_urlsafe(24)
    expires = min(until, now() + dt.timedelta(hours=SESSION_HOURS))
    with store.write() as c:
        c.execute("INSERT INTO sessions (token_hash, username, register_id, created_at, expires_at) VALUES (?,?,?,?,?)",
                  (sha(token), uname, register_id, now().isoformat(), expires.isoformat()))
    return {"token": token, "username": uname, "full_name": g["full_name"], "roles": json.loads(g["roles"]),
            "register_id": register_id, "expires_at": expires.isoformat()}


def session(store: Store, token: str | None) -> dict:
    if not token:
        raise EdgeError("Sign in at the till.", 401)
    with store.connect() as c:
        s = c.execute("SELECT s.*, g.roles FROM sessions s JOIN grants g ON g.username=s.username WHERE token_hash=?",
                      (sha(token),)).fetchone()
    if not s or dt.datetime.fromisoformat(s["expires_at"]) <= now():
        raise EdgeError("Session ended. Sign in again.", 401)
    return dict(s)


# ---------------------------------------------------------------- selling

def sell(store: Store, sess: dict, *, client_key: str, lines: list[dict], payments: list[dict]) -> dict:
    roles = json.loads(sess["roles"])
    if not set(roles) & {"cashier", "branch_manager"}:
        raise EdgeError("This account cannot sell.", 403)
    try:
        event_id = str(uuid.UUID(client_key))
    except (ValueError, TypeError) as exc:
        raise EdgeError("client_key must be a UUID.") from exc
    policy = store.get_meta("policy") or {}
    max_disc = Decimal(str(policy.get("cashier_max_discount_pct", 0)))
    allow_negative = bool(policy.get("allow_negative_stock", False))
    with store.write() as c:
        prev = c.execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone()
        if prev:  # idempotent resend from a till
            return _receipt(prev)
        priced, total = [], Decimal(0)
        need: dict[int, Decimal] = {}
        for ln in lines:
            row = c.execute("SELECT data FROM items WHERE id=?", (int(ln["product_id"]),)).fetchone()
            if not row:
                raise EdgeError("Product not in the offline catalog.")
            item = json.loads(row["data"])
            q = qty(ln["quantity"])
            if q <= 0 or (not item["sold_by_weight"] and q != q.to_integral_value()):
                raise EdgeError(f"Invalid quantity for {item['name']}.")
            pct = Decimal(str(ln.get("discount_pct") or 0))
            if pct > max_disc:
                raise EdgeError(f"Discounts above {max_disc}% need the central system (manager override is online only).", 403)
            gross = money(item["price"])
            unit = money(gross * (100 - pct) / 100) if pct else gross
            total += line_amount(q, unit)
            need[item["id"]] = need.get(item["id"], Decimal(0)) + q
            priced.append({"product_id": item["id"], "name": item["name"], "uom": item["uom"], "quantity": str(q),
                           "unit_price": str(unit), "list_price": str(gross), "discount_pct": str(pct)})
        for pid, q in need.items():
            have = available(store, c, pid)
            if q > have and not allow_negative:
                name = next(p["name"] for p in priced if p["product_id"] == pid)
                raise EdgeError(f"Not enough {name} (offline estimate {have}).", 409)
        noncash = sum((money(p["amount"]) for p in payments if p["method"] != "cash"), Decimal(0))
        cash_tendered = sum((money(p["amount"]) for p in payments if p["method"] == "cash"), Decimal(0))
        for p in payments:
            if p["method"] not in {"cash", "mobile_money", "card"}:
                raise EdgeError("Unknown payment method.")
            if p["method"] != "cash" and not (p.get("reference") or "").strip():
                raise EdgeError("Reference required for mobile money / card.")
        if noncash > total:
            raise EdgeError("Non-cash payments exceed the total.")
        cash_due = total - noncash
        if cash_tendered < cash_due:
            raise EdgeError(f"Payment short by {cash_due - cash_tendered}.")
        pays = [{"method": p["method"], "amount": str(money(p["amount"])), "reference": p.get("reference"),
                 "verification": "pending (captured offline)"} for p in payments if p["method"] != "cash"]
        if cash_due:
            pays.append({"method": "cash", "amount": str(cash_due), "tendered": str(cash_tendered),
                         "change": str(cash_tendered - cash_due)})
        reg = int(sess["register_id"])
        seq = c.execute("SELECT next FROM receipt_seq WHERE register_id=?", (reg,)).fetchone()
        n = seq["next"] if seq else 1
        c.execute("INSERT INTO receipt_seq (register_id, next) VALUES (?, ?) ON CONFLICT(register_id) DO UPDATE SET next=excluded.next",
                  (reg, n + 1))
        reg_name = next((r["name"] for r in store.get_meta("registers") or [] if r["id"] == reg), str(reg))
        local_no = f"{reg_name}-OFF-{n:05d}"
        created = now().isoformat()
        payload = {"register_id": reg, "cashier_username": sess["username"], "local_created_at": created,
                   "local_receipt_no": local_no, "lines": priced, "payments": pays,
                   "price_version": store.get_meta("price_version"), "total": str(total)}
        c.execute("""INSERT INTO outbox (event_id, created_at, register_id, cashier, local_receipt_no, payload)
                     VALUES (?,?,?,?,?,?)""", (event_id, created, reg, sess["username"], local_no, json.dumps(payload)))
        row = c.execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone()
    return _receipt(row)


def _receipt(row) -> dict:
    p = json.loads(row["payload"])
    return {"event_id": row["event_id"], "local_receipt_no": row["local_receipt_no"], "status": row["status"],
            "central_number": row["central_number"], "created_at": row["created_at"], **p}


# ---------------------------------------------------------------- sync

class Syncer:
    """Push pending events then pull a fresh snapshot.  Safe to call repeatedly."""

    def __init__(self, store: Store, client: httpx.Client):
        self.store = store
        self.client = client

    def push(self, batch: int = 50) -> dict:
        with self.store.connect() as c:
            rows = c.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY created_at LIMIT ?", (batch,)).fetchall()
        if not rows:
            return {"sent": 0}
        events = [{"event_id": r["event_id"], "kind": "sale", "payload": json.loads(r["payload"])} for r in rows]
        try:
            resp = self.client.post("/api/edge/events", json={"events": events}, timeout=30)
            resp.raise_for_status()
            acks = {a["event_id"]: a for a in resp.json()["acks"]}
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            with self.store.write() as c:
                for r in rows:
                    c.execute("UPDATE outbox SET attempts=attempts+1, last_error=? WHERE event_id=?",
                              (str(exc)[:300], r["event_id"]))
            raise
        with self.store.write() as c:
            for r in rows:
                a = acks.get(r["event_id"])
                if not a:
                    continue
                if a["status"] in {"posted", "posted_with_conflict"}:
                    c.execute("""UPDATE outbox SET status='acked', central_number=?, ack=?, acked_at=?, attempts=attempts+1
                                 WHERE event_id=?""", (a.get("central_number"), json.dumps(a, default=str), now().isoformat(), r["event_id"]))
                else:  # rejected: kept for manager review, never retried silently
                    c.execute("UPDATE outbox SET status='rejected', ack=?, last_error=?, attempts=attempts+1 WHERE event_id=?",
                              (json.dumps(a, default=str), a.get("error"), r["event_id"]))
            self.store.set_meta(c, "last_push_ok", now().isoformat())
        return {"sent": len(rows), "acks": list(acks.values())}

    def pull(self) -> None:
        resp = self.client.get("/api/edge/snapshot", timeout=30)
        resp.raise_for_status()
        apply_snapshot(self.store, resp.json())

    def sync_once(self) -> dict:
        result: dict[str, Any] = {"ok": False}
        try:
            total = 0
            while True:
                r = self.push()
                total += r["sent"]
                if r["sent"] == 0:
                    break
            self.pull()
            with self.store.write() as c:
                self.store.set_meta(c, "last_sync_ok", now().isoformat())
            result.update(ok=True, pushed=total)
        except httpx.HTTPError as exc:
            result["error"] = f"offline: {type(exc).__name__}"
            with self.store.write() as c:
                self.store.set_meta(c, "last_sync_error", {"at": now().isoformat(), "error": str(exc)[:200]})
        return result


def status(store: Store) -> dict:
    with store.connect() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status, count(*) n FROM outbox GROUP BY status")}
        oldest = c.execute("SELECT min(created_at) m FROM outbox WHERE status='pending'").fetchone()["m"]
    last_ok = store.get_meta("last_sync_ok")
    last_err = (store.get_meta("last_sync_error") or {}).get("at")
    online = bool(last_ok) and (now() - dt.datetime.fromisoformat(last_ok)).total_seconds() < 90         and not (last_err and last_err > last_ok)
    return {"online": online, "last_sync_ok": last_ok, "last_error": store.get_meta("last_sync_error"),
            "pending": counts.get("pending", 0), "acked": counts.get("acked", 0), "rejected": counts.get("rejected", 0),
            "oldest_pending": oldest, "snapshot_at": store.get_meta("snapshot_at"),
            "price_version": store.get_meta("price_version"), "branch": store.get_meta("branch"),
            "state": "sync healthy" if online and not counts.get("pending") else ("sync pending" if online else "offline")}
