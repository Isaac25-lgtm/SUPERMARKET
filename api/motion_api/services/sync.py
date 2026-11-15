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
from decimal import Decimal

from .. import tryton as t
from ..audit import RequestMeta, record
from ..errors import Forbidden, Invalid, NotFound, Unauthenticated
from ..security import Principal, sha256, utcnow
from ..settings import get_settings
from . import catalog, common, pos


def register_device(actor: Principal, *, branch_id: int, name: str) -> dict:
    actor.require("sync.devices", action="sync.device.register")
    common.branch(branch_id)
    token = "edge_" + secrets.token_urlsafe(32)
    row = t.sql_one("""INSERT INTO motion.edge_device (branch_id, name, token_hash, created_by) VALUES (%s,%s,%s,%s)
                       RETURNING id, branch_id, name, created_at""", (branch_id, name.strip(), sha256(token), actor.user_id))
    record(actor=actor, action="sync.device.register", target_type="edge_device", target_id=row["id"], branch_id=branch_id,
           after={"name": name})
    return {**row, "token": token, "note": "Copy this token into the branch edge configuration now; it is not shown again."}


def revoke_device(actor: Principal, device_id: int, reason: str) -> None:
    actor.require("sync.devices", action="sync.device.revoke")
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
    return [r for r in rows if actor.can_branch(r["branch_id"]) or actor.has("sync.devices")]


def authenticate_device(token: str | None) -> dict:
    if not token or not token.startswith("edge_"):
        raise Unauthenticated("Edge device token required.")
    row = t.sql_one("SELECT * FROM motion.edge_device WHERE token_hash=%s", (sha256(token),))
    if not row or row["revoked_at"]:
        raise Unauthenticated("Edge device is not registered or was revoked.")
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
                     WHERE u.status='active' AND r.role_code IN ('cashier','branch_manager')
                       AND (u.all_branches OR ub.branch_id=%s)
                     GROUP BY 1,2,3""", (b["id"],))
    return {
        "branch": {"id": b["id"], "code": b["code"], "name": b["name"]},
        "registers": common.registers(b["id"]),
        "items": [{"id": i["id"], "name": i["name"], "barcode": i["barcode"], "plu": i["plu"], "sku": i["sku"],
                   "uom": i["uom"], "sold_by_weight": i["sold_by_weight"], "price": i["shelf_price"], "vat": i["vat"],
                   "category": i["category"], "on_hand": stock.get((b["storage_location_id"], i["id"]), Decimal(0))}
                  for i in items if i["salable"] and i["shelf_price"] is not None],
        "grants": [{"username": u["username"], "full_name": u["full_name"], "roles": u["roles"],
                    "verifier": u["password_hash"], "offline_until": grants_until} for u in users],
        "policy": pos.pos_policy(), "methods": list(pos.METHOD_NAMES), "price_version": pos.price_version(),
        "server_time": utcnow(), "brand": settings.brand_name, "test_data": settings.demo_mode,
    }


def _ingest_sale(device: dict, event: dict) -> dict:
    p = event["payload"]
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
    items = {i["id"]: i for i in catalog._rows(ids=[int(x["product_id"]) for x in p["lines"]], active=None)}
    lines = []
    for x in p["lines"]:
        pid = int(x["product_id"])
        if pid not in items:
            raise Invalid(f"Unknown product {pid} on offline sale.")
        charged = common.money(x["unit_price"])
        list_price = common.money(x.get("list_price") or charged)
        if items[pid]["shelf_price"] is not None and list_price != items[pid]["shelf_price"]:
            conflicts.append({"type": "price_version_divergence", "product_id": pid, "offline_price": str(list_price),
                              "central_price": str(items[pid]["shelf_price"]), "edge_price_version": p.get("price_version")})
        lines.append({"product_id": pid, "quantity": common.qty(x["quantity"]), "unit_gross": charged,
                      "list_gross": list_price, "discount_pct": Decimal(str(x.get("discount_pct") or 0))})
    payments = [{"method": x["method"], "amount": common.money(x["amount"]), "reference": x.get("reference"),
                 "offline": True} for x in p["payments"]]
    local_at = dt.datetime.fromisoformat(p["local_created_at"])
    sale_date = local_at.astimezone(common.local_tz()).date()
    cashier = {"id": user["id"], "tryton_user_id": user["tryton_user_id"], "tryton_employee_id": user["tryton_employee_id"]}
    result = pos.post_sale(register=reg, cashier=cashier, lines=lines, payments=payments, sale_date=sale_date,
                           idempotency_key=f"edge:{event['event_id']}", source="edge", shift_id=None,
                           enforce_stock=False, edge_device_id=device["id"], local_created_at=local_at)
    conflicts += [{"type": "oversold", **{k: str(v) for k, v in c.items()}} for c in result.get("stock_conflicts", [])]
    for c in conflicts:
        if c["type"] != "oversold":  # oversell discrepancies are recorded by post_sale
            t.execute("""INSERT INTO motion.discrepancy (kind, branch_id, source_type, source_id, product_id, detail)
                         VALUES ('sync_conflict', %s, 'pos_sale', %s, %s, %s::jsonb)""",
                      (reg["branch_id"], str(result["id"]), c.get("product_id"), json.dumps(c)))
    record(action="sync.sale.posted", target_type="pos_sale", target_id=result["number"], branch_id=reg["branch_id"],
           amount=result["total"], reason=f"offline sale by {user['username']} on {reg['name']}",
           after={"event_id": event["event_id"], "local_receipt": p.get("local_receipt_no"), "conflicts": len(conflicts),
                  "local_created_at": p["local_created_at"]},
           meta=RequestMeta(request_id=f"edge-{device['id']}"))
    return {"status": "posted_with_conflict" if conflicts else "posted", "central_number": result["number"],
            "pos_sale_id": result["id"], "conflicts": conflicts, "replayed": result.get("replayed", False)}


def ingest_event(device: dict, event: dict) -> dict:
    """Process one event idempotently. Runs inside its own transaction."""
    eid = str(event.get("event_id") or "")
    if len(eid) < 32:
        raise Invalid("event_id (UUID) required.")
    prev = t.sql_one("SELECT status, result FROM motion.sync_inbox WHERE event_id=%s::uuid", (eid,))
    if prev:
        return {"event_id": eid, **prev["result"], "duplicate": True}
    if event.get("kind") != "sale":
        raise Invalid("Unsupported event kind.")
    result = _ingest_sale(device, event)
    t.execute("""INSERT INTO motion.sync_inbox (edge_device_id, event_id, kind, payload, status, result)
                 VALUES (%s,%s::uuid,%s,%s::jsonb,%s,%s::jsonb)""",
              (device["id"], eid, event["kind"], json.dumps(event["payload"], default=str), result["status"],
               json.dumps(result, default=str)))
    return {"event_id": eid, **json.loads(json.dumps(result, default=str)), "duplicate": False}


def sync_status(actor: Principal) -> dict:
    actor.require_any("sync.devices", "pos.history.branch", "finance.view")
    return {"devices": list_devices(actor),
            "recent": t.sql("""SELECT i.event_id, i.received_at, i.status, i.result->>'central_number' AS number, d.name AS device
                               FROM motion.sync_inbox i JOIN motion.edge_device d ON d.id=i.edge_device_id
                               ORDER BY i.id DESC LIMIT 50""")}
