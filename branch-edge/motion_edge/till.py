"""Till operations at the branch box, shaped like the head-office till API.

The branch's checkout computers always talk to the box, online or not. Each action is
recorded locally (durably) as an immutable outbox event and forwarded to head office,
which replays it through the same POS, shift, approval and refund rules it applies to
online tills:

* ``shift_open`` / ``shift_close``: blind cash count; head office recomputes expected cash.
* ``sale``: attached to the open shift; discounts above the cashier limit carry the
  manager who approved them in person (verified here against that manager's grant).
* ``refund``: against a receipt recorded by this box, approved in person by a branch
  manager, paid out in cash (never more than the cash taken on the receipt).
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import ROUND_HALF_EVEN, Decimal, DecimalException

from argon2.exceptions import InvalidHashError, VerificationError

from . import service
from .service import EdgeError, _hasher, line_amount, money, now, qty
from .store import Store

INTERRUPTED_AFTER_HOURS = 14
TILL_PERMISSIONS = {
    "cashier": ["pos.sell", "pos.shift", "pos.refund.request", "pos.refund.execute"],
    "branch_manager": ["pos.override.approve"],  # approves at the cashier's till; does not run a till
}


# ---------------------------------------------------------------- identity

def me(store: Store, sess: dict) -> dict:
    roles = json.loads(sess["roles"])
    branch = store.get_meta("branch") or {}
    perms = sorted({p for r in roles for p in TILL_PERMISSIONS.get(r, [])})
    return {"id": 0, "username": sess["username"], "full_name": sess.get("full_name") or sess["username"], "roles": roles,
            "permissions": perms, "all_branches": False, "branch_ids": [branch.get("id")] if branch else [],
            "must_change_password": False, "branches": [branch] if branch else [],
            "registers": [{"id": r["id"], "name": r["name"], "branch_id": r.get("branch_id")}
                          for r in store.get_meta("registers") or []]}


def meta(store: Store) -> dict:
    branch = store.get_meta("branch") or {}
    return {"brand": store.get_meta("brand") or "Motion Retail", "company": "", "currency": "UGX",
            "timezone": "Africa/Kampala", "test_data": bool(store.get_meta("test_data")), "ai_llm_enabled": False,
            "open_demo": False, "session_idle_minutes": 15, "deployment": "edge", "edge": True,
            "branch": branch}


def _verify_manager(store: Store, username: str, password: str, *, cashier: str) -> dict:
    """In-person approval: the branch manager types their own credentials at the till.

    The (deliberately slow) password check runs outside the write lock so other tills keep
    selling; callers re-check inside their transaction that the grant did not change."""
    uname = (username or "").strip().lower()
    cutoff = (now() - dt.timedelta(minutes=15)).isoformat()
    with store.connect() as c:
        if c.execute("SELECT count(*) AS n FROM login_failure WHERE username=? AND attempted_at>?",
                     (uname, cutoff)).fetchone()["n"] >= 5:
            raise EdgeError("Too many wrong manager passwords. Try again in 15 minutes.", 429)
        g = c.execute("SELECT * FROM grants WHERE username=?", (uname,)).fetchone()
    try:
        ok = bool(g) and _hasher.verify(g["verifier"], password or "")
    except (VerificationError, InvalidHashError):
        ok = False
    if not ok:
        with store.write() as c:
            c.execute("INSERT INTO login_failure VALUES (?,?,?)", (uname, "manager-approval", now().isoformat()))
        raise EdgeError("Manager username or password is incorrect.", 401)
    if dt.datetime.fromisoformat(g["offline_until"]) <= now():
        raise EdgeError("This manager's offline access has expired; reconnect the internet.", 401)
    if "branch_manager" not in json.loads(g["roles"]):
        raise EdgeError("Only a branch manager can approve this at the till.", 403)
    if uname == cashier:
        raise EdgeError("You cannot approve your own request.", 403)
    return dict(g)


def _grant_unchanged(c, manager: dict) -> None:
    row = c.execute("SELECT verifier, offline_until FROM grants WHERE username=?", (manager["username"],)).fetchone()
    if not row or row["verifier"] != manager["verifier"] or row["offline_until"] != manager["offline_until"]:
        raise EdgeError("The manager's access changed. Ask them to approve again.", 409)


# ---------------------------------------------------------------- shifts

def _reg(store: Store, register_id: int) -> dict:
    reg = next((r for r in store.get_meta("registers") or [] if r["id"] == register_id), None)
    if not reg:
        raise EdgeError("Unknown till for this branch.", 400)
    return reg


def _shift_view(store: Store, c, row) -> dict:
    counts = c.execute("""SELECT count(*) FILTER (WHERE kind='sale') AS sales, count(*) FILTER (WHERE kind='refund') AS refunds
                          FROM outbox WHERE json_extract(payload, '$.shift_event_id') = ?""", (row["id"],)).fetchone()
    branch = store.get_meta("branch") or {}
    opened = dt.datetime.fromisoformat(row["opened_at"])
    return {"id": row["id"], "register_id": row["register_id"], "register_name": _reg(store, row["register_id"])["name"],
            "branch_name": branch.get("name"), "opened_at": row["opened_at"], "opening_float": row["opening_float"],
            "sales": counts["sales"], "refunds": counts["refunds"], "status": row["status"],
            "interrupted": row["status"] == "open" and opened < now() - dt.timedelta(hours=INTERRUPTED_AFTER_HOURS)}


def current_shift(store: Store, sess: dict) -> dict | None:
    with store.connect() as c:
        row = c.execute("SELECT * FROM shifts WHERE cashier=? AND status='open'", (sess["username"],)).fetchone()
        return _shift_view(store, c, row) if row else None


def _emit(store: Store, c, *, kind: str, register_id: int, cashier: str, label: str, payload: dict,
          event_id: str | None = None) -> str:
    event_id = event_id or str(uuid.uuid4())
    c.execute("""INSERT INTO outbox (event_id, created_at, register_id, cashier, local_receipt_no, payload, kind)
                 VALUES (?,?,?,?,?,?,?)""",
              (event_id, payload["local_created_at"], register_id, cashier, label, json.dumps(payload), kind))
    return event_id


def open_shift(store: Store, sess: dict, *, register_id: int, opening_float) -> dict:
    service.check_fresh(store)
    if "cashier" not in json.loads(sess["roles"]):
        raise EdgeError("Only cashiers open till shifts. Managers approve discounts and refunds on the cashier's till.", 403)
    try:
        float_ = money(opening_float)
    except (DecimalException, ValueError):
        raise EdgeError("Enter the opening float in shillings.") from None
    if float_ < 0:
        raise EdgeError("Opening float cannot be negative.")
    reg = _reg(store, int(register_id))
    with store.write() as c:
        sess = service._active_session(c, sess["token_hash"])
        if c.execute("SELECT 1 FROM shifts WHERE cashier=? AND status='open'", (sess["username"],)).fetchone():
            raise EdgeError("You already have an open shift. Close it first.", 409)
        if c.execute("SELECT 1 FROM shifts WHERE register_id=? AND status='open'", (reg["id"],)).fetchone():
            raise EdgeError("Another cashier has an open shift on this till.", 409)
        for other in store.get_meta("open_shifts") or []:
            if other["register_id"] != reg["id"] or other["username"].lower() == sess["username"]:
                continue
            # Shifts the box itself opened show up at head office too; only an online-only shift blocks.
            ours = c.execute("""SELECT 1 FROM shifts WHERE register_id=? AND cashier=? AND opened_at>?""",
                             (reg["id"], other["username"].lower(), (now() - dt.timedelta(days=2)).isoformat())).fetchone()
            if not ours:
                raise EdgeError(f"{reg['name']} still has {other['full_name'].split(' (')[0]}'s shift open at head office. "
                                "Use your own till, or ask the branch manager to close that shift online first.", 409)
        shift_id, created = str(uuid.uuid4()), now().isoformat()
        payload = {"shift_event_id": shift_id, "register_id": reg["id"], "cashier_username": sess["username"],
                   "opening_float": str(float_), "local_created_at": created}
        _emit(store, c, kind="shift_open", register_id=reg["id"], cashier=sess["username"],
              label=f"SHIFT-{shift_id[:8]}-OPEN", payload=payload, event_id=shift_id)
        c.execute("INSERT INTO shifts (id, register_id, cashier, opened_at, opening_float) VALUES (?,?,?,?,?)",
                  (shift_id, reg["id"], sess["username"], created, str(float_)))
        c.execute("UPDATE sessions SET register_id=? WHERE token_hash=?", (reg["id"], sess["token_hash"]))
        row = c.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        return _shift_view(store, c, row)


def _expected_cash(c, shift) -> Decimal:
    expected = Decimal(shift["opening_float"])
    for r in c.execute("""SELECT kind, payload FROM outbox WHERE kind IN ('sale','refund')
                          AND json_extract(payload, '$.shift_event_id') = ?""", (shift["id"],)):
        p = json.loads(r["payload"])
        if r["kind"] == "sale":
            expected += sum((Decimal(x["amount"]) for x in p["payments"] if x["method"] == "cash"), Decimal(0))
        else:
            expected -= Decimal(p["total"])
    return expected


def close_shift(store: Store, sess: dict, shift_id: str, *, counted_cash, cash_drop, note: str | None) -> dict:
    try:
        counted, drop = money(counted_cash), money(cash_drop or 0)
    except (DecimalException, ValueError):
        raise EdgeError("Enter the counted cash in shillings.") from None
    if counted < 0 or drop < 0 or drop > counted:
        raise EdgeError("Counted cash and cash drop must be positive and the drop cannot exceed the count.")
    with store.write() as c:
        sess = service._active_session(c, sess["token_hash"])
        shift = c.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not shift or shift["cashier"] != sess["username"]:
            raise EdgeError("Shift not found.", 404)
        if shift["status"] != "open":
            raise EdgeError("This shift is already closed.", 409)
        expected = _expected_cash(c, shift)
        variance = counted - expected
        created = now().isoformat()
        payload = {"shift_event_id": shift_id, "register_id": shift["register_id"], "cashier_username": sess["username"],
                   "counted_cash": str(counted), "cash_drop": str(drop), "note": note or None,
                   "expected_cash_at_box": str(expected), "local_created_at": created}
        _emit(store, c, kind="shift_close", register_id=shift["register_id"], cashier=sess["username"],
              label=f"SHIFT-{shift_id[:8]}-CLOSE", payload=payload)
        c.execute("""UPDATE shifts SET status='closed', closed_at=?, counted_cash=?, cash_drop=?, expected_cash=?,
                     variance=?, note=? WHERE id=?""",
                  (created, str(counted), str(drop), str(expected), str(variance), note, shift_id))
        row = c.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        return {**_shift_view(store, c, row), "counted_cash": str(counted), "expected_cash": str(expected),
                "variance": str(variance)}


def open_shift_for(store: Store, sess: dict) -> dict:
    with store.connect() as c:
        row = c.execute("SELECT * FROM shifts WHERE cashier=? AND status='open'", (sess["username"],)).fetchone()
    if not row:
        raise EdgeError("Open your shift on this till first.", 409)
    if row["register_id"] != sess["register_id"]:
        with store.write() as c:
            c.execute("UPDATE sessions SET register_id=? WHERE token_hash=?", (row["register_id"], sess["token_hash"]))
        sess = {**sess, "register_id": row["register_id"]}
    return {"shift": dict(row), "sess": sess}


# ---------------------------------------------------------------- catalog & scanning

def catalog(store: Store, register_id: int) -> dict:
    reg = _reg(store, int(register_id))
    with store.connect() as c:
        reserved = service.reservations(c)
        items = []
        for r in c.execute("SELECT id, data FROM items"):
            it = json.loads(r["data"])
            it["on_hand"] = str(service.available(store, c, r["id"], reserved))
            items.append(it)
    return {"register": reg, "items": items, "policy": store.get_meta("policy") or {},
            "methods": store.get_meta("methods") or [], "price_version": store.get_meta("price_version")}


def barcode_candidates(raw: str) -> list[str]:
    code = "".join((raw or "").split())
    out = [code]
    if code.isdigit() and len(code) == 12:
        out.append("0" + code)
    if code.isdigit() and len(code) == 13 and code.startswith("0"):
        out.append(code[1:])
    return out


def _gtin_valid(code: str) -> bool:
    if not code.isdigit() or len(code) not in {8, 12, 13, 14}:
        return False
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def lookup(store: Store, code: str) -> dict:
    """Barcode, PLU or SKU; scale labels (prefix 2, EAN-13) carry the weight or price."""
    code = (code or "").strip()
    if not code:
        raise EdgeError("Scan or type a code.")
    forms = barcode_candidates(code)
    with store.connect() as c:
        items = [json.loads(r["data"]) for r in c.execute("SELECT data FROM items")]
    hits = [i for i in items if code in {i.get("sku"), i.get("plu")} or set(i.get("barcodes") or []) & set(forms)]
    scale = None
    if not hits and len(code) == 13 and _gtin_valid(code) and code[0] == "2":
        layout = (store.get_meta("policy") or {}).get("scale_barcode") or {
            "weight_prefixes": ["20", "21", "22", "23", "24"], "price_prefixes": ["25", "26", "27", "28", "29"],
            "price_multiplier": 10}
        plu, value = code[2:7], int(code[7:12])
        hits = [i for i in items if i.get("plu") == plu]
        if hits and code[:2] in layout["weight_prefixes"]:
            scale = {"kind": "weight", "quantity": str(qty(Decimal(value) / 1000))}
        elif hits and code[:2] in layout["price_prefixes"]:
            amount = money(value * layout.get("price_multiplier", 10))
            scale = {"kind": "price", "amount": str(amount),
                     "quantity": str(qty(amount / Decimal(str(hits[0]["price"]))))}
    if not hits:
        raise EdgeError(f"Unknown barcode {code}. Ask the inventory manager to register it on the product.", 404)
    if len(hits) > 1:
        raise EdgeError("That code matches more than one product; fix the catalog.", 409)
    return {**hits[0], "scale": scale}


# ---------------------------------------------------------------- manager approval for discounts

def request_override(store: Store, sess: dict, *, register_id: int, lines: list[dict], manager_username: str,
                     manager_password: str, reason: str) -> dict:
    if not reason or len(reason.strip()) < 3:
        raise EdgeError("Give a reason for the discount.")
    manager = _verify_manager(store, manager_username, manager_password, cashier=sess["username"])
    with store.write() as c:
        sess = service._active_session(c, sess["token_hash"])
        if register_id != sess["register_id"]:
            raise EdgeError("Approve the discount on the till where the shift is open.", 409)
        _grant_unchanged(c, manager)
        c.execute("""INSERT INTO overrides (cashier, register_id, manager, basket, reason, created_at)
                     VALUES (?,?,?,?,?,?)""",
                  (sess["username"], register_id, manager["username"], json.dumps(service.basket_signature(lines)),
                   reason.strip(), now().isoformat()))
        oid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return {"approval_id": oid, "approved_by": manager["full_name"]}


# ---------------------------------------------------------------- receipts

def _vat(gross: Decimal, vat: bool, rate_pct: Decimal) -> Decimal:
    if not vat or not rate_pct:
        return Decimal(0)
    return (gross * rate_pct / (100 + rate_pct)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)


def receipt_view(store: Store, row, *, refunded: dict[int, Decimal] | None = None) -> dict:
    p = json.loads(row["payload"])
    policy = store.get_meta("policy") or {}
    rate = Decimal(str(policy.get("vat_rate_pct", 18)))
    branch = store.get_meta("branch") or {}
    reg = next((r for r in store.get_meta("registers") or [] if r["id"] == row["register_id"]), {"name": str(row["register_id"])})
    with store.connect() as c:
        g = c.execute("SELECT full_name FROM grants WHERE username=?", (row["cashier"],)).fetchone()
    lines, tax = [], Decimal(0)
    sign = Decimal(-1) if row["kind"] == "refund" else Decimal(1)
    for i, ln in enumerate(p["lines"], start=1):
        gross = Decimal(ln["gross"]) if "gross" in ln else line_amount(ln["quantity"], ln["unit_price"])
        tax += _vat(gross, bool(ln.get("vat")), rate)
        lines.append({"id": i, "name": ln.get("name"), "uom": ln.get("uom"), "product_id": ln["product_id"],
                      "quantity": str(sign * Decimal(ln["quantity"])), "unit_price": ln.get("unit_price"),
                      "discount_pct": ln.get("discount_pct", "0"), "amount": str(sign * gross),
                      "refunded_qty": str((refunded or {}).get(i, Decimal(0)))})
    payments = p.get("payments") or [{"method": "cash", "amount": str(-Decimal(p["total"]))}]
    total = Decimal(p["total"]) * sign
    return {"id": row["event_id"], "number": row["local_receipt_no"], "kind": row["kind"], "posted_at": row["created_at"],
            "brand": store.get_meta("brand"), "branch_name": branch.get("name"), "register_name": reg["name"],
            "cashier_name": g["full_name"] if g else row["cashier"], "lines": lines, "total": str(total),
            "total_tax": str(tax * sign), "payments": payments, "footer": policy.get("receipt_footer"),
            "journal_entry": row["central_number"] or "awaiting upload",
            "central_number": row["central_number"], "sync_status": row["status"],
            "test_data": bool(store.get_meta("test_data")), "replayed": False}


def _refunded(c, sale_event_id: str) -> dict[int, Decimal]:
    out: dict[int, Decimal] = {}
    for r in c.execute("""SELECT payload FROM outbox WHERE kind='refund'
                          AND json_extract(payload, '$.original_event_id') = ?""", (sale_event_id,)):
        for ln in json.loads(r["payload"])["lines"]:
            out[int(ln["line_no"])] = out.get(int(ln["line_no"]), Decimal(0)) + Decimal(ln["quantity"])
    return out


def find_receipt(store: Store, number: str) -> dict:
    n = (number or "").strip().upper()
    with store.connect() as c:
        row = c.execute("""SELECT * FROM outbox WHERE kind='sale' AND (upper(local_receipt_no)=? OR upper(central_number)=?)""",
                        (n, n)).fetchone()
        if not row:
            raise EdgeError("No sale with that receipt number at this branch box. Refunds of older or other-branch "
                            "receipts are done online through the approval inbox.", 404)
        return receipt_view(store, row, refunded=_refunded(c, row["event_id"]))


def refund(store: Store, sess: dict, *, sale_event_id: str, lines: list[dict], reason: str,
           manager_username: str, manager_password: str, client_key: str) -> dict:
    """Refund in person: the manager approves at the till and the cashier pays the cash out."""
    try:
        event_id = str(uuid.UUID(client_key))
    except (ValueError, TypeError) as exc:
        raise EdgeError("client_key must be a UUID.") from exc
    if not reason or len(reason.strip()) < 5:
        raise EdgeError("Explain why the goods are being refunded.")
    if "cashier" not in json.loads(sess["roles"]):
        raise EdgeError("Refunds are paid out by a cashier at their own till.", 403)
    with store.connect() as c:
        prev = c.execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone()
    if prev:
        return receipt_view(store, prev)
    ctx = open_shift_for(store, sess)
    shift, sess = ctx["shift"], ctx["sess"]
    manager = _verify_manager(store, manager_username, manager_password, cashier=sess["username"])
    with store.write() as c:
        sess = service._active_session(c, sess["token_hash"])
        _grant_unchanged(c, manager)
        if c.execute("SELECT 1 FROM outbox WHERE event_id=?", (event_id,)).fetchone():
            raise EdgeError("This refund was already recorded.", 409)
        if not c.execute("SELECT 1 FROM shifts WHERE id=? AND status='open'", (shift["id"],)).fetchone():
            raise EdgeError("Open your shift on this till first.", 409)
        sale = c.execute("SELECT * FROM outbox WHERE event_id=? AND kind='sale'", (sale_event_id,)).fetchone()
        if not sale:
            raise EdgeError("Original sale not found at this branch box.", 404)
        p = json.loads(sale["payload"])
        done = _refunded(c, sale_event_id)
        refunded_gross: dict[int, Decimal] = {}
        for r in c.execute("""SELECT payload FROM outbox WHERE kind='refund'
                              AND json_extract(payload, '$.original_event_id') = ?""", (sale_event_id,)):
            for ln in json.loads(r["payload"])["lines"]:
                refunded_gross[int(ln["line_no"])] = refunded_gross.get(int(ln["line_no"]), Decimal(0)) + Decimal(ln["gross"])
        plan, total, seen = [], Decimal(0), set()
        for ln in lines:
            no = int(ln["line_id"])
            if no in seen or not 1 <= no <= len(p["lines"]):
                raise EdgeError("Each receipt line can be refunded once per request.")
            seen.add(no)
            orig = p["lines"][no - 1]
            try:
                q = qty(ln["quantity"])
            except (DecimalException, ValueError, KeyError):
                raise EdgeError("Check the refund quantities.") from None
            sold = qty(orig["quantity"])
            left = sold - done.get(no, Decimal(0))
            if q <= 0 or q > left:
                raise EdgeError(f"Refund quantity exceeds what can still be refunded ({left}).")
            line_gross = line_amount(orig["quantity"], orig["unit_price"])
            left_gross = line_gross - refunded_gross.get(no, Decimal(0))
            # Same rule as head office: proportional share, and the last units close the line exactly.
            gross = left_gross if q == left else min(line_amount(q / sold, line_gross), left_gross)
            total += gross
            plan.append({"line_no": no, "product_id": orig["product_id"], "name": orig.get("name"), "uom": orig.get("uom"),
                         "quantity": str(q), "unit_price": orig["unit_price"], "gross": str(gross),
                         "vat": bool(orig.get("vat"))})
        if not plan:
            raise EdgeError("Select at least one line to refund.")
        cash_taken = sum((Decimal(x["amount"]) for x in p["payments"] if x["method"] == "cash"), Decimal(0))
        cash_refunded = sum((Decimal(json.loads(r["payload"])["total"]) for r in c.execute(
            "SELECT payload FROM outbox WHERE kind='refund' AND json_extract(payload, '$.original_event_id') = ?",
            (sale_event_id,))), Decimal(0))
        if total > cash_taken - cash_refunded:
            raise EdgeError(f"Only UGX {max(cash_taken - cash_refunded, Decimal(0)):,} of this receipt was paid in cash and "
                            "can be refunded at the till. Mobile money or card parts are refunded by Finance.", 409)
        created = now().isoformat()
        local_no = service.next_receipt_no(store, c, shift["register_id"])
        payload = {"shift_event_id": shift["id"], "register_id": shift["register_id"],
                   "cashier_username": sess["username"], "manager_username": manager["username"],
                   "original_event_id": sale_event_id, "original_local_no": sale["local_receipt_no"],
                   "reason": reason.strip(), "lines": plan, "total": str(total), "local_receipt_no": local_no,
                   "local_created_at": created}
        _emit(store, c, kind="refund", register_id=shift["register_id"], cashier=sess["username"], label=local_no,
              payload=payload, event_id=event_id)
        row = c.execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone()
    return receipt_view(store, row)


def recent_refunds(store: Store, sess: dict) -> list[dict]:
    with store.connect() as c:
        rows = c.execute("""SELECT * FROM outbox WHERE kind='refund' AND cashier=? ORDER BY created_at DESC LIMIT 20""",
                         (sess["username"],)).fetchall()
    out = []
    for r in rows:
        p = json.loads(r["payload"])
        out.append({"id": r["event_id"], "ref": r["local_receipt_no"], "title": f"Refund on receipt {p['original_local_no']}",
                    "amount": p["total"], "created_at": r["created_at"], "status": "executed",
                    "status_label": "Paid out" if r["status"] != "rejected" else "Needs review",
                    "manager": p["manager_username"]})
    return out
