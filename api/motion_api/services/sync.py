"""Central side of branch-edge offline operation.

* Devices: registered per branch by an authorised admin; a random token is
  shown once and only its SHA-256 is stored; revocation is immediate.
* Snapshot (edge pull): branch catalog & shelf prices, stock snapshot,
  registers, payment methods, POS policy and *time-limited* cashier grants
  (argon2 verifier + expiry).  A suspended user disappears from the next
  snapshot; an edge refuses grants past ``offline_until`` even if it never
  reconnects, which bounds how long a revoked account could keep selling.
* Events (edge push): each sale has an immutable UUID ``event_id`` used as the
  idempotency key; a resend returns the original acknowledgement.  Sales are
  posted through the same Tryton POS workflow as online sales, attributed to
  the original cashier/register with the local timestamp preserved.
  Oversell, price divergence, unverified non-cash tenders and cashier status
  changes are posted (the goods have physically left) but logged as conflicts
  for manager/accountant review — never silently discarded.
* Branch-box tills also send ``shift_open`` / ``shift_close`` (replayed through the same
  shift functions: cash session, float adjustment, blind count, variance) and ``refund``
  (replayed as request → in-person manager approval → pay-out). An event whose dependency
  (its shift, or the original sale) has not arrived yet is answered ``retry``.
"""
from __future__ import annotations

import datetime as dt
import json
import secrets
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from .. import tryton as t
from ..audit import RequestMeta, record
from ..errors import Conflict, Forbidden, Invalid, NotFound, Unauthenticated
from ..security import Principal, load_principal, sha256, utcnow
from ..settings import get_settings
from . import approvals, catalog, common, pos


class NotReady(Exception):
    """An event arrived before the event it depends on; the edge retries it later."""


def register_device(actor: Principal, *, branch_id: int, name: str) -> dict:
    actor.require("sync.devices", action="sync.device.register")
    common.branch(branch_id)
    actor.require_branch(branch_id, action="sync.device.register")
    token = "edge_" + secrets.token_urlsafe(32)
    row = t.sql_one("""INSERT INTO motion.edge_device (branch_id, name, token_hash, created_by) VALUES (%s,%s,%s,%s)
                       RETURNING id, branch_id, name, created_at""", (branch_id, name.strip(), sha256(token), actor.user_id))
    record(actor=actor, action="sync.device.register", target_type="edge_device", target_id=row["id"], branch_id=branch_id,
           after={"name": name})
    return {**row, "token": token, "note": "Copy this token into the branch edge configuration now; it is not shown again."}


def revoke_device(actor: Principal, device_id: int, reason: str) -> None:
    actor.require("sync.devices", action="sync.device.revoke")
    dev = t.sql_one("SELECT branch_id FROM motion.edge_device WHERE id=%s", (device_id,))
    if not dev:
        raise NotFound("Device not found.")
    actor.require_branch(dev["branch_id"], action="sync.device.revoke", target_type="edge_device", target_id=device_id)
    n = t.execute("UPDATE motion.edge_device SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL", (device_id,))
    if not n:
        raise NotFound("Device not found or already revoked.")
    record(actor=actor, action="sync.device.revoke", target_type="edge_device", target_id=device_id, reason=reason)


def list_devices(actor: Principal) -> list[dict]:
    actor.require_any("sync.devices", "dashboard.consolidated")
    rows = t.sql("""SELECT d.id, d.branch_id, b.code AS branch_code, d.name, d.created_at, d.last_seen_at, d.revoked_at,
                           (SELECT count(*) FROM motion.sync_inbox i WHERE i.edge_device_id=d.id) AS events,
                           (SELECT count(*) FROM motion.sync_inbox i WHERE i.edge_device_id=d.id AND i.status='posted_with_conflict') AS conflicts
                    FROM motion.edge_device d JOIN motion_branch b ON b.id=d.branch_id ORDER BY d.id""")
    return [r for r in rows if actor.can_branch(r["branch_id"])]


def authenticate_device(token: str | None, *, for_event: bool = False) -> dict:
    if not token or not token.startswith("edge_"):
        raise Unauthenticated("Edge device token required.")
    locking = " FOR SHARE" if for_event else ""
    row = t.sql_one("SELECT * FROM motion.edge_device WHERE token_hash=%s" + locking, (sha256(token),))
    if not row or row["revoked_at"]:
        raise Unauthenticated("Edge device is not registered or was revoked.")
    if not for_event:
        t.execute("UPDATE motion.edge_device SET last_seen_at=now() WHERE id=%s", (row["id"],))
    return row


def snapshot(device: dict) -> dict:
    settings = get_settings()
    b = common.branch(device["branch_id"])
    items = catalog._rows()
    stock = common.stock_on_hand([b["storage_location_id"]], [i["id"] for i in items])
    grants_until = utcnow() + dt.timedelta(hours=settings.offline_grant_hours)
    users = t.sql("""SELECT u.username, u.full_name, u.password_hash,
                            array_agg(DISTINCT r.role_code) AS roles
                     FROM motion.app_user u JOIN motion.user_role r ON r.user_id=u.id
                     LEFT JOIN motion.user_branch ub ON ub.user_id=u.id
                      WHERE u.status='active' AND NOT u.must_change_password AND r.role_code IN ('cashier','branch_manager')
                       AND (u.all_branches OR ub.branch_id=%s)
                     GROUP BY 1,2,3""", (b["id"],))
    return {
        "branch": {"id": b["id"], "code": b["code"], "name": b["name"]},
        "registers": common.registers(b["id"]),
        # Tills with a shift open at head office (e.g. opened online while the box was off), so the box does
        # not start a second shift on the same till.
        "open_shifts": t.sql("""SELECT s.register_id, u.username, u.full_name FROM motion.shift s
                                JOIN motion.app_user u ON u.id = s.cashier_id
                                WHERE s.status='open' AND s.branch_id=%s""", (b["id"],)),
        "items": [{"id": i["id"], "name": i["name"], "barcode": i["barcode"], "barcodes": i["barcodes"], "plu": i["plu"], "sku": i["sku"],
                   "uom": i["uom"], "sold_by_weight": i["sold_by_weight"], "price": i["shelf_price"], "vat": i["vat"],
                   "category": i["category"], "on_hand": stock.get((b["storage_location_id"], i["id"]), Decimal(0))}
                  for i in items if i["salable"] and i["shelf_price"] is not None],
        "grants": [{"username": u["username"], "full_name": u["full_name"], "roles": u["roles"],
                    "verifier": u["password_hash"], "offline_until": grants_until} for u in users],
        "policy": {**pos.pos_policy(), "vat_rate_pct": _vat_rate_pct(), "scale_barcode": catalog._scale_layout()},
        "methods": list(pos.METHOD_NAMES), "price_version": pos.price_version(),
        "server_time": utcnow(), "brand": settings.brand_name, "test_data": settings.demo_mode,
    }


def _vat_rate_pct() -> Decimal:
    """The sales VAT rate the receipts at the branch box show (head office posts the real tax)."""
    row = t.sql_one("""SELECT rate FROM account_tax WHERE type='percentage' AND parent IS NULL
                       AND name NOT ILIKE 'input%%' ORDER BY id LIMIT 1""")
    return (Decimal(row["rate"]) * 100).normalize() if row and row["rate"] is not None else Decimal(18)


class OfflineLine(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=15, decimal_places=3)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=0)
    list_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=0)
    discount_pct: Decimal = Field(default=Decimal(0), ge=0, le=100, decimal_places=4)


class OfflinePayment(BaseModel):
    method: Literal["cash", "mobile_money", "card"]
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    reference: str | None = Field(default=None, max_length=120)


class OfflineOverride(BaseModel):
    manager_username: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=300)


class OfflineSale(BaseModel):
    register_id: int = Field(gt=0)
    cashier_username: str = Field(min_length=1, max_length=32)
    local_created_at: AwareDatetime
    local_receipt_no: str = Field(min_length=1, max_length=120)
    lines: list[OfflineLine] = Field(min_length=1, max_length=200)
    payments: list[OfflinePayment] = Field(max_length=6)
    price_version: str | None = Field(default=None, max_length=120)
    total: Decimal = Field(ge=0, max_digits=14, decimal_places=0)
    shift_event_id: str | None = Field(default=None, max_length=40)
    override: OfflineOverride | None = None


class ShiftOpenEvent(BaseModel):
    shift_event_id: str = Field(min_length=32, max_length=40)
    register_id: int = Field(gt=0)
    cashier_username: str = Field(min_length=1, max_length=32)
    opening_float: Decimal = Field(ge=0, max_digits=14, decimal_places=0)
    local_created_at: AwareDatetime


class ShiftCloseEvent(BaseModel):
    shift_event_id: str = Field(min_length=32, max_length=40)
    register_id: int = Field(gt=0)
    cashier_username: str = Field(min_length=1, max_length=32)
    counted_cash: Decimal = Field(ge=0, max_digits=14, decimal_places=0)
    cash_drop: Decimal = Field(default=Decimal(0), ge=0, max_digits=14, decimal_places=0)
    note: str | None = Field(default=None, max_length=500)
    expected_cash_at_box: Decimal | None = Field(default=None, max_digits=14, decimal_places=0)
    local_created_at: AwareDatetime


class RefundLineEvent(BaseModel):
    line_no: int = Field(gt=0)
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=15, decimal_places=3)
    gross: Decimal = Field(ge=0, max_digits=14, decimal_places=0)


class RefundEvent(BaseModel):
    shift_event_id: str = Field(min_length=32, max_length=40)
    register_id: int = Field(gt=0)
    cashier_username: str = Field(min_length=1, max_length=32)
    manager_username: str = Field(min_length=1, max_length=32)
    original_event_id: str = Field(min_length=32, max_length=40)
    original_local_no: str = Field(max_length=120)
    reason: str = Field(min_length=5, max_length=500)
    lines: list[RefundLineEvent] = Field(min_length=1, max_length=200)
    total: Decimal = Field(ge=0, max_digits=14, decimal_places=0)
    local_receipt_no: str = Field(max_length=120)
    local_created_at: AwareDatetime


def _edge_meta(device: dict) -> RequestMeta:
    return RequestMeta(request_id=f"edge-{device['id']}", ip=f"edge-device-{device['id']}")


def _person(device: dict, username: str) -> Principal:
    user = t.sql_one("SELECT id FROM motion.app_user WHERE lower(username)=%s", (username.lower(),))
    if not user:
        raise Invalid(f"Unknown user {username} on a branch-box event.")
    return load_principal(user["id"], meta=_edge_meta(device))


def _depends_on(device: dict, event_id: str | None, what: str) -> dict | None:
    """Result of an earlier event from this device; NotReady if it has not been ingested yet."""
    if not event_id:
        return None
    row = t.sql_one("SELECT edge_device_id, status, result FROM motion.sync_inbox WHERE event_id=%s", (event_id,))
    if not row:
        raise NotReady(f"Waiting for the {what} to arrive first.")
    if row["edge_device_id"] != device["id"]:
        raise Invalid(f"The {what} belongs to another branch box.")
    return {"status": row["status"], **(row["result"] or {})}


def _local_date(at: dt.datetime) -> dt.date:
    return at.astimezone(common.local_tz()).date()


def _conflict(branch_id: int, source_type: str, source_id: str, detail: dict, product_id: int | None = None) -> None:
    t.execute("""INSERT INTO motion.discrepancy (kind, branch_id, source_type, source_id, product_id, detail)
                 VALUES ('sync_conflict', %s, %s, %s, %s, %s::jsonb)""",
              (branch_id, source_type, source_id, product_id, json.dumps(detail, default=str)))


def _ingest_shift_open(device: dict, event_id: str, ev: ShiftOpenEvent) -> dict:
    reg = common.register(ev.register_id)
    if reg["branch_id"] != device["branch_id"]:
        raise Invalid("The till belongs to another branch.")
    cashier = _person(device, ev.cashier_username)
    existing = t.sql_one("SELECT * FROM motion.shift WHERE cashier_id=%s AND status='open'", (cashier.user_id,))
    if existing:
        if existing["register_id"] == reg["id"]:
            # Already open on this till (e.g. opened online during a branch-box restart): continue it.
            _conflict(reg["branch_id"], "shift", str(existing["id"]),
                      {"type": "shift_already_open", "edge_shift": ev.shift_event_id, "cashier": cashier.username})
            return {"status": "posted_with_conflict", "shift_id": existing["id"],
                    "conflicts": [{"type": "shift_already_open"}]}
        raise Invalid(f"{cashier.username} still has a shift open on another till. Close it, then the branch box "
                      "shift can be recorded.")
    shift = pos.open_shift(cashier, register_id=reg["id"], opening_float=ev.opening_float,
                           on_date=_local_date(ev.local_created_at))
    t.execute("UPDATE motion.shift SET opened_at=%s WHERE id=%s", (ev.local_created_at, shift["id"]))
    return {"status": "posted", "shift_id": shift["id"], "central_number": f"Shift {shift['id']}"}


def _ingest_shift_close(device: dict, event_id: str, ev: ShiftCloseEvent) -> dict:
    opened = _depends_on(device, ev.shift_event_id, "shift opening")
    shift_id = opened.get("shift_id") if opened else None
    if not shift_id:
        raise Invalid("The shift opening was not recorded at head office; the close needs manager review.")
    cashier = _person(device, ev.cashier_username)
    shift = t.sql_one("SELECT * FROM motion.shift WHERE id=%s", (shift_id,))
    if shift["status"] != "open":
        return {"status": "posted_with_conflict", "shift_id": shift_id, "conflicts": [{"type": "shift_already_closed"}]}
    closed = pos.close_shift(cashier, shift_id, counted_cash=ev.counted_cash, cash_drop=ev.cash_drop, note=ev.note,
                             on_date=_local_date(ev.local_created_at))
    t.execute("UPDATE motion.shift SET closed_at=%s WHERE id=%s", (ev.local_created_at, shift_id))
    conflicts = []
    if ev.expected_cash_at_box is not None and common.money(closed["expected_cash"]) != ev.expected_cash_at_box:
        conflicts.append({"type": "expected_cash_differs", "box": str(ev.expected_cash_at_box),
                          "head_office": str(closed["expected_cash"])})
        _conflict(shift["branch_id"], "shift", str(shift_id), conflicts[-1])
    return {"status": "posted_with_conflict" if conflicts else "posted", "shift_id": shift_id,
            "central_number": f"Shift {shift_id}", "variance": str(closed["variance"]), "conflicts": conflicts}


def _ingest_refund(device: dict, event_id: str, ev: RefundEvent) -> dict:
    opened = _depends_on(device, ev.shift_event_id, "shift opening")
    original = _depends_on(device, ev.original_event_id, "original sale")
    if not original or not original.get("pos_sale_id"):
        raise Invalid("The original sale was not recorded at head office; this refund needs manager review.")
    reg = common.register(ev.register_id)
    cashier = _person(device, ev.cashier_username)
    manager = _person(device, ev.manager_username)
    if not opened or not opened.get("shift_id"):
        raise Invalid("The shift for this refund was not recorded at head office; it needs manager review.")
    sale_id = int(original["pos_sale_id"])
    line_ids = [r["id"] for r in t.sql("SELECT id FROM motion.pos_sale_line WHERE pos_sale_id=%s ORDER BY id", (sale_id,))]
    lines = []
    for ln in ev.lines:
        if ln.line_no > len(line_ids):
            raise Invalid("A refund line does not exist on the original receipt.")
        lines.append({"line_id": line_ids[ln.line_no - 1], "quantity": str(ln.quantity)})
    req = pos.request_refund(cashier, sale_id=sale_id, lines=lines,
                             reason=f"{ev.reason} (approved in person by {manager.username} at {ev.local_receipt_no})")
    decided = approvals.act(manager, req["id"], decision="approve",
                            comment=f"Approved in person at the till ({ev.local_receipt_no}).")
    if decided["status"] != "approved":
        raise Invalid("The refund policy needs more approvers than the branch manager; it needs review at head office.")
    result = pos.execute_refund(cashier, approval_id=req["id"], register_id=reg["id"], idempotency_key=f"edge:{event_id}",
                                _sale_date=_local_date(ev.local_created_at))
    t.execute("UPDATE motion.pos_sale SET source='edge', edge_device_id=%s, local_created_at=%s WHERE id=%s",
              (device["id"], ev.local_created_at, result["id"]))
    conflicts = []
    if common.money(-Decimal(result["total"])) != ev.total:
        conflicts.append({"type": "refund_amount_differs", "box": str(ev.total), "head_office": str(-Decimal(result["total"]))})
        _conflict(reg["branch_id"], "pos_sale", str(result["id"]), conflicts[-1])
    return {"status": "posted_with_conflict" if conflicts else "posted", "central_number": result["number"],
            "pos_sale_id": result["id"], "approval": req["ref"], "conflicts": conflicts}


def _ingest_sale(device: dict, event_id: str, sale: OfflineSale) -> dict:
    p = sale.model_dump()
    reg = common.register(int(p["register_id"]))
    if reg["branch_id"] != device["branch_id"]:
        raise Forbidden("Register belongs to another branch.", action="sync.sale.wrong_branch",
                        target_type="edge_device", target_id=str(device["id"]), branch_id=device["branch_id"])
    user = t.sql_one("SELECT * FROM motion.app_user WHERE lower(username)=%s", (p["cashier_username"].lower(),))
    if not user:
        raise Invalid("Unknown cashier on offline sale.")
    conflicts = []
    if user["status"] != "active":
        conflicts.append({"type": "cashier_inactive_at_sync", "username": user["username"]})
    roles = {r["role_code"] for r in t.sql("SELECT role_code FROM motion.user_role WHERE user_id=%s", (user["id"],))}
    if not roles & {"cashier", "branch_manager"}:
        conflicts.append({"type": "cashier_role_missing_at_sync", "username": user["username"]})
    if not user["all_branches"] and not t.sql_one("SELECT 1 FROM motion.user_branch WHERE user_id=%s AND branch_id=%s",
                                                 (user["id"], device["branch_id"])):
        conflicts.append({"type": "cashier_branch_missing_at_sync", "username": user["username"]})
    items = {i["id"]: i for i in catalog._rows(ids=[int(x["product_id"]) for x in p["lines"]], active=None)}
    override_ok = False
    if sale.override is not None:
        mgr = t.sql_one("SELECT id FROM motion.app_user WHERE lower(username)=%s", (sale.override.manager_username.lower(),))
        try:
            manager = load_principal(mgr["id"], meta=_edge_meta(device)) if mgr else None
        except Unauthenticated:
            manager = None
        override_ok = bool(manager and manager.has("pos.override.approve") and manager.can_branch(device["branch_id"])
                           and manager.username.lower() != user["username"].lower())
        if not override_ok:
            conflicts.append({"type": "override_not_valid_at_sync", "manager": sale.override.manager_username})
    lines = []
    for x in p["lines"]:
        pid = int(x["product_id"])
        if pid not in items:
            raise Invalid(f"Unknown product {pid} on offline sale.")
        charged = common.money(x["unit_price"])
        list_price = charged if x["list_price"] is None else common.money(x["list_price"])
        if not items[pid]["sold_by_weight"] and x["quantity"] != x["quantity"].to_integral_value():
            raise Invalid("Unit goods must have a whole quantity.")
        if charged != common.money(list_price * (100 - x["discount_pct"]) / 100):
            raise Invalid("The recorded price and discount disagree.")
        if x["discount_pct"] > Decimal(str(pos.pos_policy().get("cashier_max_discount_pct", 0))) \
                and not override_ok:
            conflicts.append({"type": "discount_policy_divergence", "product_id": pid})
        if items[pid]["shelf_price"] is not None and list_price != items[pid]["shelf_price"]:
            conflicts.append({"type": "price_version_divergence", "product_id": pid, "offline_price": str(list_price),
                              "central_price": str(items[pid]["shelf_price"]), "edge_price_version": p.get("price_version")})
        lines.append({"product_id": pid, "quantity": common.qty(x["quantity"]), "unit_gross": charged,
                      "list_gross": list_price, "discount_pct": Decimal(str(x.get("discount_pct") or 0))})
    payments = [{"method": x["method"], "amount": common.money(x["amount"]), "reference": x.get("reference"),
                 "offline": True} for x in p["payments"]]
    if any(x["method"] != "cash" and not (x["reference"] or "").strip() for x in payments):
        raise Invalid("A non-cash tender needs a transaction reference.")
    basket_total = sum((common.line_amount(x["quantity"], x["unit_gross"]) for x in lines), Decimal(0))
    if basket_total != sale.total or basket_total != sum((x["amount"] for x in payments), Decimal(0)):
        raise Invalid("The recorded basket and payments do not balance.")
    local_at = sale.local_created_at
    if local_at > utcnow() + dt.timedelta(minutes=5):
        raise Invalid("The sale time is ahead of the central clock. Check the branch device clock.")
    sale_date = local_at.astimezone(common.local_tz()).date()
    cashier = {"id": user["id"], "tryton_user_id": user["tryton_user_id"], "tryton_employee_id": user["tryton_employee_id"]}
    opened = _depends_on(device, sale.shift_event_id, "shift opening")
    shift_id = opened.get("shift_id") if opened else None
    if sale.shift_event_id and not shift_id:
        conflicts.append({"type": "shift_missing_at_sync", "edge_shift": sale.shift_event_id})
    if shift_id:
        t.execute("UPDATE motion.shift SET activity_seq = activity_seq + 1 WHERE id=%s", (shift_id,))
    result = pos.post_sale(register=reg, cashier=cashier, lines=lines, payments=payments, sale_date=sale_date,
                           idempotency_key=f"edge:{event_id}", source="edge", shift_id=shift_id,
                           enforce_stock=False, edge_device_id=device["id"], local_created_at=local_at)
    conflicts += [{"type": "oversold", **{k: str(v) for k, v in c.items()}} for c in result.get("stock_conflicts", [])]
    for c in conflicts:
        if c["type"] != "oversold":  # oversell discrepancies are recorded by post_sale
            t.execute("""INSERT INTO motion.discrepancy (kind, branch_id, source_type, source_id, product_id, detail)
                         VALUES ('sync_conflict', %s, 'pos_sale', %s, %s, %s::jsonb)""",
                      (reg["branch_id"], str(result["id"]), c.get("product_id"), json.dumps(c)))
    record(action="sync.sale.posted", target_type="pos_sale", target_id=result["number"], branch_id=reg["branch_id"],
           amount=result["total"], reason=f"offline sale by {user['username']} on {reg['name']}",
           after={"event_id": event_id, "local_receipt": p["local_receipt_no"], "conflicts": len(conflicts),
                  "local_created_at": p["local_created_at"]},
           meta=RequestMeta(request_id=f"edge-{device['id']}"))
    return {"status": "posted_with_conflict" if conflicts else "posted", "central_number": result["number"],
            "pos_sale_id": result["id"], "conflicts": conflicts, "replayed": result.get("replayed", False)}


def event_uuid(event: dict) -> str:
    try:
        return str(uuid.UUID(str(event.get("event_id", ""))))
    except ValueError:
        raise Invalid("The edge event needs a valid UUID.") from None


def ingest_event(device: dict, eid: str, event: dict, *, rejection: str | None = None) -> dict:
    """Record one stable outcome per immutable event, in its own transaction."""
    kind = event["kind"] if isinstance(event.get("kind"), str) else ""
    payload = event.get("payload")
    encoded = json.dumps(payload, allow_nan=False)
    claimed = t.execute("""INSERT INTO motion.sync_inbox (edge_device_id, event_id, kind, payload, status)
                            VALUES (%s,%s,%s,%s::jsonb,'rejected') ON CONFLICT DO NOTHING""",
                         (device["id"], eid, kind, encoded))
    if not claimed:
        prev = t.sql_one("SELECT * FROM motion.sync_inbox WHERE event_id=%s", (eid,))
        if prev["edge_device_id"] != device["id"] or prev["kind"] != kind or prev["payload"] != payload:
            raise Conflict("This event identifier does not match its original submission.")
        return {"event_id": eid, **prev["result"], "duplicate": True}
    if rejection is None:
        handlers = {"sale": (OfflineSale, _ingest_sale), "shift_open": (ShiftOpenEvent, _ingest_shift_open),
                    "shift_close": (ShiftCloseEvent, _ingest_shift_close), "refund": (RefundEvent, _ingest_refund)}
        if kind not in handlers:
            raise Invalid("Unsupported offline event kind.")
        model, handler = handlers[kind]
        try:
            parsed = model.model_validate(payload)
        except ValidationError:
            raise Invalid(f"The branch-box {kind.replace('_', ' ')} has invalid or missing fields.") from None
        result = handler(device, eid, parsed)
    else:
        result = {"status": "rejected", "error": rejection}
    t.execute("UPDATE motion.sync_inbox SET status=%s, result=%s::jsonb WHERE event_id=%s",
              (result["status"], json.dumps(result, default=str), eid))
    return {"event_id": eid, **result, "duplicate": False}


def sync_status(actor: Principal) -> dict:
    actor.require_any("sync.devices", "pos.history.branch", "finance.view")
    return {"devices": list_devices(actor),
            "recent": t.sql("""SELECT i.event_id, i.received_at, i.status, i.result->>'central_number' AS number, d.name AS device
                               FROM motion.sync_inbox i JOIN motion.edge_device d ON d.id=i.edge_device_id
                               WHERE (%s OR d.branch_id = ANY(%s))
                               ORDER BY i.id DESC LIMIT 50""", (actor.all_branches, sorted(actor.branch_ids)))}
