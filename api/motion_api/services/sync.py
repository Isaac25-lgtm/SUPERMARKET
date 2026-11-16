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
from ..security import Principal, sha256, utcnow
from ..settings import get_settings
from . import catalog, common, pos


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
        "items": [{"id": i["id"], "name": i["name"], "barcode": i["barcode"], "barcodes": i["barcodes"], "plu": i["plu"], "sku": i["sku"],
                   "uom": i["uom"], "sold_by_weight": i["sold_by_weight"], "price": i["shelf_price"], "vat": i["vat"],
                   "category": i["category"], "on_hand": stock.get((b["storage_location_id"], i["id"]), Decimal(0))}
                  for i in items if i["salable"] and i["shelf_price"] is not None],
        "grants": [{"username": u["username"], "full_name": u["full_name"], "roles": u["roles"],
                    "verifier": u["password_hash"], "offline_until": grants_until} for u in users],
        "policy": pos.pos_policy(), "methods": list(pos.METHOD_NAMES), "price_version": pos.price_version(),
        "server_time": utcnow(), "brand": settings.brand_name, "test_data": settings.demo_mode,
    }


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


class OfflineSale(BaseModel):
    register_id: int = Field(gt=0)
    cashier_username: str = Field(min_length=1, max_length=32)
    local_created_at: AwareDatetime
    local_receipt_no: str = Field(min_length=1, max_length=120)
    lines: list[OfflineLine] = Field(min_length=1, max_length=200)
    payments: list[OfflinePayment] = Field(max_length=6)
    price_version: str | None = Field(default=None, max_length=120)
    total: Decimal = Field(ge=0, max_digits=14, decimal_places=0)


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
        if x["discount_pct"] > Decimal(str(pos.pos_policy().get("cashier_max_discount_pct", 0))):
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
    result = pos.post_sale(register=reg, cashier=cashier, lines=lines, payments=payments, sale_date=sale_date,
                           idempotency_key=f"edge:{event_id}", source="edge", shift_id=None,
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
        if kind != "sale":
            raise Invalid("Unsupported offline event kind.")
        try:
            sale = OfflineSale.model_validate(payload)
        except ValidationError:
            raise Invalid("The offline sale has invalid or missing fields.") from None
        result = _ingest_sale(device, eid, sale)
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
