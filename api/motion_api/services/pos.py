"""Point of sale: shifts, checkout, refunds, till overrides.

Every completed sale is processed and posted through Tryton's own
``sale.point.sale`` workflow (open -> done -> posted) in the same database
transaction as the platform's idempotency record and audit event, so a sale
either exists completely (receipt number, stock moves, revenue/VAT/payment
journal entry, COGS entry) or not at all.

Oversell protection: each product's (location, product) guard row is updated
first; two cashiers selling the last unit conflict on that row, the second
transaction is retried with a fresh snapshot and then sees zero stock.
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

from .. import tryton as t
from ..audit import record
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..security import Principal, load_principal, utcnow, verify_password
from ..settings import get_settings
from . import approvals, catalog, common, idempotency

METHOD_NAMES = {
    "cash": "Cash",
    "mobile_money": "Mobile money (pending verification)",
    "card": "Card (pending settlement)",
}
TRANSFER_TYPES = {
    "over_short": "Cash over / short", "drop": "Cash drop to safe",
    "topup": "Float top-up from safe", "bank": "Bank deposit",
}
INTERRUPTED_AFTER_HOURS = 14


def pos_policy() -> dict:
    row = t.sql_one("SELECT value FROM motion.app_setting WHERE key='pos_policy'")
    return row["value"] if row else {"cashier_max_discount_pct": 5, "allow_negative_stock": False}


def _method_ids() -> dict[str, int]:
    Method = t.model("sale.point.payment.method")
    out = {}
    for key, name in METHOD_NAMES.items():
        found = Method.search([("name", "=", name)], limit=1)
        if found:
            out[key] = found[0].id
    return out


def _transfer_type(key: str) -> int:
    TT = t.model("sale.point.cash.transfer.type")
    found = TT.search([("name", "=", TRANSFER_TYPES[key])], limit=1)
    if not found:
        raise RuntimeError(f"cash transfer type {key} not configured")
    return found[0].id


# ------------------------------------------------------------------ shifts

def _shift(shift_id: int, lock: bool = False) -> dict:
    row = t.sql_one("SELECT * FROM motion.shift WHERE id=%s" + (" FOR UPDATE" if lock else ""), (shift_id,))
    if not row:
        raise NotFound("Shift not found.")
    return row


def my_open_shift(actor: Principal) -> dict | None:  # cashier's own view: blind until counted
    row = t.sql_one("""SELECT s.*, p.name AS register_name, b.name AS branch_name FROM motion.shift s
                       JOIN sale_point p ON p.id=s.register_id JOIN motion_branch b ON b.id=s.branch_id
                       WHERE s.cashier_id=%s AND s.status='open'""", (actor.user_id,))
    if not row:
        return None
    return _with_live_totals(row)


def _with_live_totals(shift: dict, *, reveal: bool = False) -> dict:
    """Shift summary. While a shift is open the cashier sees counts only (blind cash count,
    Codex H10); money totals and the expected cash appear once the count is recorded, or
    to supervisors (``reveal``)."""
    shift = dict(shift)
    totals = t.sql_one("""SELECT count(*) FILTER (WHERE kind='sale') AS sales, count(*) FILTER (WHERE kind='refund') AS refunds,
                                 COALESCE(sum(total),0) AS net_total FROM motion.pos_sale WHERE shift_id=%s""", (shift["id"],))
    shift.update(totals)
    blind = shift["status"] == "open" and not reveal
    if blind:
        shift.pop("net_total", None)
        for k in ("expected_cash", "counted_cash", "variance"):
            shift.pop(k, None)
    elif shift["status"] == "open":
        session = t.model("sale.point.cash.session")(shift["tryton_session_id"])
        shift["expected_cash_now"] = common.money(session.start_amount + session.balance)
    shift["interrupted"] = shift["status"] == "open" and shift["opened_at"] < utcnow() - timedelta(hours=INTERRUPTED_AFTER_HOURS)
    return shift


def open_shift(actor: Principal, *, register_id: int, opening_float: Decimal, on_date=None) -> dict:
    """``on_date`` is internal (seeding dated TEST history); the API never passes it."""
    actor.require("pos.shift", action="pos.shift.open")
    reg = common.register(register_id)
    actor.require_branch(reg["branch_id"], action="pos.shift.open", target_type="register", target_id=register_id)
    opening_float = common.money(opening_float)
    if opening_float < 0:
        raise Invalid("Opening float cannot be negative.")
    if t.sql_one("SELECT 1 AS x FROM motion.shift WHERE cashier_id=%s AND status='open'", (actor.user_id,)):
        raise Conflict("You already have an open shift. Close it first.")
    if t.sql_one("SELECT 1 AS x FROM motion.shift WHERE register_id=%s AND status='open'", (register_id,)):
        raise Conflict("Another cashier has an open shift on this register.")
    Point = t.model("sale.point")
    Session = t.model("sale.point.cash.session")
    Transfer = t.model("sale.point.cash.transfer")
    point = Point(register_id)
    with t.set_context(**common.tryton_context(reg["branch_id"])):
        session = Session.get_current(point)
        start = common.money(session.start_amount + session.balance)
        diff = opening_float - start
        if diff:
            Transfer.create([{"point": point.id, "session": session.id, "amount": diff, "date": on_date or common.today(),
                              "type": _transfer_type("topup" if diff > 0 else "drop")}])
    row = t.sql_one("""INSERT INTO motion.shift (branch_id, register_id, cashier_id, tryton_session_id, status,
                       opening_float, session_start_amount) VALUES (%s,%s,%s,%s,'open',%s,%s) RETURNING *""",
                    (reg["branch_id"], register_id, actor.user_id, session.id, opening_float, start))
    record(actor=actor, action="pos.shift.open", target_type="shift", target_id=row["id"], branch_id=reg["branch_id"],
           amount=opening_float, after={"register": reg["name"], "float": opening_float, "float_adjustment": diff})
    return _with_live_totals(row)


def close_shift(actor: Principal, shift_id: int, *, counted_cash: Decimal, cash_drop: Decimal = Decimal(0),
                note: str | None = None, on_date=None) -> dict:
    shift = _shift(shift_id, lock=True)
    own = shift["cashier_id"] == actor.user_id
    if own:
        actor.require("pos.shift", action="pos.shift.close")
    else:
        actor.require("pos.shift.reconcile", action="pos.shift.force_close")
        if not note:
            raise Invalid("A note is required when closing another cashier's shift.")
    actor.require_branch(shift["branch_id"], action="pos.shift.close", target_type="shift", target_id=shift_id)
    if shift["status"] != "open":
        raise Conflict("This shift is already closed.")
    counted = common.money(counted_cash)
    drop = common.money(cash_drop)
    if counted < 0 or drop < 0 or drop > counted:
        raise Invalid("Counted cash and cash drop must be positive and the drop cannot exceed the count.")
    Session = t.model("sale.point.cash.session")
    Transfer = t.model("sale.point.cash.transfer")
    with t.set_context(**common.tryton_context(shift["branch_id"])):
        session = Session(shift["tryton_session_id"])
        expected = common.money(session.start_amount + session.balance)
        variance = counted - expected
        transfers = []
        if variance:
            transfers.append({"point": shift["register_id"], "session": session.id, "amount": variance,
                              "date": on_date or common.today(), "type": _transfer_type("over_short")})
        if drop:
            transfers.append({"point": shift["register_id"], "session": session.id, "amount": -drop,
                              "date": on_date or common.today(), "type": _transfer_type("drop")})
        created = Transfer.create(transfers) if transfers else []
        session.end_amount = counted - drop
        session.save()
        Session.close([session])
        Session.post([session])
        common.expect_state(session, "posted", what="cash session close")
    status = "closed"
    t.execute("""UPDATE motion.shift SET status=%s, closed_at=now(), counted_cash=%s, expected_cash=%s, variance=%s,
                 variance_transfer_id=%s, close_note=%s, closed_by=%s WHERE id=%s""",
              (status, counted, expected, variance, created[0].id if variance and created else None, note,
               actor.user_id, shift_id))
    if variance:
        t.execute("""INSERT INTO motion.discrepancy (kind, branch_id, source_type, source_id, amount, detail, created_by)
                     VALUES ('cash_variance', %s, 'shift', %s, %s, %s::jsonb, %s)""",
                  (shift["branch_id"], str(shift_id), variance,
                   json.dumps({"expected": str(expected), "counted": str(counted), "cashier_id": shift["cashier_id"]}),
                   actor.user_id))
    record(actor=actor, action="pos.shift.close" if own else "pos.shift.force_close", target_type="shift",
           target_id=shift_id, branch_id=shift["branch_id"], amount=variance, reason=note,
           after={"expected": expected, "counted": counted, "variance": variance, "cash_drop": drop})
    return _with_live_totals(_shift(shift_id), reveal=True)


def reconcile_shift(actor: Principal, shift_id: int, note: str) -> dict:
    actor.require("pos.shift.reconcile", action="pos.shift.reconcile")
    shift = _shift(shift_id, lock=True)
    actor.require_branch(shift["branch_id"], action="pos.shift.reconcile", target_type="shift", target_id=shift_id)
    if shift["status"] != "closed":
        raise Conflict("Only closed shifts can be reconciled.")
    if shift["cashier_id"] == actor.user_id:
        raise Forbidden("You cannot reconcile your own shift.", action="pos.shift.reconcile.self",
                        target_type="shift", target_id=str(shift_id))
    if not note or len(note.strip()) < 3:
        raise Invalid("Add a reconciliation note.")
    t.execute("UPDATE motion.shift SET status='reconciled', reconciled_by=%s, reconciled_at=now(), reconcile_note=%s WHERE id=%s",
              (actor.user_id, note, shift_id))
    t.execute("""UPDATE motion.discrepancy SET status='resolved', resolution='reviewed', resolution_note=%s,
                 resolved_by=%s, resolved_at=now() WHERE kind='cash_variance' AND source_id=%s AND status='open'""",
              (note, actor.user_id, str(shift_id)))
    record(actor=actor, action="pos.shift.reconcile", target_type="shift", target_id=shift_id,
           branch_id=shift["branch_id"], amount=shift["variance"], reason=note)
    return _with_live_totals(_shift(shift_id), reveal=True)


def list_shifts(actor: Principal, *, branch_id: int | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
    actor.require_any("pos.history.branch", "pos.history.own")
    rows = t.sql("""SELECT s.*, u.full_name AS cashier_name, p.name AS register_name, b.code AS branch_code
                    FROM motion.shift s JOIN motion.app_user u ON u.id=s.cashier_id JOIN sale_point p ON p.id=s.register_id
                    JOIN motion_branch b ON b.id=s.branch_id
                    WHERE (%s::int IS NULL OR s.branch_id=%s) AND (%s::text IS NULL OR s.status=%s)
                    ORDER BY s.opened_at DESC LIMIT %s""", (branch_id, branch_id, status, status, limit))
    out = []
    for r in rows:
        if not actor.has("pos.history.branch") and r["cashier_id"] != actor.user_id:
            continue
        if not actor.can_branch(r["branch_id"]):
            continue
        r["interrupted"] = r["status"] == "open" and r["opened_at"] < utcnow() - timedelta(hours=INTERRUPTED_AFTER_HOURS)
        out.append(r)
    return out


# ------------------------------------------------------------------ till catalog

def till_catalog(actor: Principal, register_id: int) -> dict:
    actor.require("pos.sell")
    reg = common.register(register_id)
    actor.require_branch(reg["branch_id"], action="pos.catalog", target_type="register", target_id=register_id)
    items = catalog._rows()
    stock = common.stock_on_hand([reg["storage_location_id"]], [i["id"] for i in items]) if items else {}
    out = [{
        "id": i["id"], "name": i["name"], "sku": i["sku"], "barcode": i["barcode"], "barcodes": i["barcodes"], "plu": i["plu"],
        "category": i["category"], "uom": i["uom"], "sold_by_weight": i["sold_by_weight"],
        "price": i["shelf_price"], "vat": i["vat"],
        "on_hand": stock.get((reg["storage_location_id"], i["id"]), Decimal(0)),
    } for i in items if i["salable"] and i["shelf_price"] is not None]
    return {"register": reg, "items": out, "policy": pos_policy(),
            "methods": list(METHOD_NAMES), "price_version": price_version()}


def price_version() -> str:
    row = t.sql_one("SELECT COALESCE(max(id),0) AS v FROM motion.price_change")
    return str(row["v"])


# ------------------------------------------------------------------ core posting

def _lock_guards(location_id: int, product_ids: list[int]) -> None:
    for pid in sorted(set(product_ids)):
        t.execute("""INSERT INTO motion.stock_guard (location_id, product_id, version) VALUES (%s,%s,1)
                     ON CONFLICT (location_id, product_id) DO UPDATE SET version = motion.stock_guard.version + 1""",
                  (location_id, pid))


def _line_amount(q: Decimal, unit_gross: Decimal) -> Decimal:
    return common.line_amount(q, unit_gross)


def _hold_shift(actor: Principal, register_id: int, *, purpose: str) -> dict:
    """Serialize a sale or refund with shift close (Codex H10): bump the open shift's
    activity counter. A close running concurrently either waits for this transaction
    and then fails serialization (and is retried with a snapshot that includes the
    sale), or has already committed, in which case this update fails serialization and
    the retry finds no open shift."""
    shift = t.sql_one("""UPDATE motion.shift SET activity_seq = activity_seq + 1
                         WHERE cashier_id=%s AND register_id=%s AND status='open' RETURNING *""",
                      (actor.user_id, register_id))
    if not shift:
        raise Conflict(f"Open a shift on this register {purpose}.")
    return shift


_DEFERRED: list[int] = []


def post_deferred() -> int:
    """Bulk-post sales created with defer_post=True (TEST DATA seeding)."""
    if not _DEFERRED:
        return 0
    Sale = t.model("sale.point.sale")
    sales = Sale.browse(list(_DEFERRED))
    with t.set_context(**common.tryton_context()):
        for branch_id in {s.point.branch.id for s in sales}:
            with t.set_context(motion_branch=branch_id):
                Sale.post([s for s in sales if s.point.branch.id == branch_id])
    n = len(_DEFERRED)
    _DEFERRED.clear()
    return n


def post_sale(*, register: dict, cashier: dict, lines: list[dict], payments: list[dict], sale_date,
              idempotency_key: str, source: str, shift_id: int | None, enforce_stock: bool,
              kind: str = "sale", refund_of: int | None = None, approval_id: int | None = None,
              edge_device_id: int | None = None, local_created_at=None, defer_post: bool = False,
              cash_session_id: int | None = None) -> dict:
    """Create, process and post a Tryton POS sale.

    ``lines``: [{product_id, quantity (Decimal, negative for refunds), unit_gross (charged), list_gross,
                 discount_pct, refund_of_line, and for refunds: unit_net + tax_ids of the original line}]
    ``payments``: [{method: cash|mobile_money|card, amount (Decimal), reference}]
    Idempotency is the caller's job (operation ledger for online commands, event id for
    edge sync); a reused posting key here is a bug and is refused.
    Returns the stored pos_sale row plus Tryton ids. Raises Conflict on oversell.
    """
    if t.sql_one("SELECT 1 AS x FROM motion.pos_sale WHERE idempotency_key=%s", (idempotency_key,)):
        raise Conflict("This transaction was already posted.")
    branch_id = register["branch_id"]
    location = register["storage_location_id"]
    product_ids = [line["product_id"] for line in lines]
    _lock_guards(location, product_ids)
    conflicts = []
    if kind == "sale":
        need: dict[int, Decimal] = {}
        for line in lines:
            need[line["product_id"]] = need.get(line["product_id"], Decimal(0)) + line["quantity"]
        on_hand = common.stock_on_hand([location], list(need))
        names = common.product_names(set(need))
        for pid, q in need.items():
            have = on_hand.get((location, pid), Decimal(0))
            if q > have:
                conflicts.append({"product_id": pid, "name": names.get(pid, {}).get("name"), "requested": q, "available": have})
        if conflicts and enforce_stock:
            raise Conflict("Not enough stock: " + ", ".join(f"{c['name']} (have {c['available']})" for c in conflicts),
                           details={"shortages": conflicts})
    Sale = t.model("sale.point.sale")
    Line = t.model("sale.point.sale.line")
    Payment = t.model("sale.point.payment")
    Tax = t.model("account.tax")
    Product = t.model("product.product")
    methods = _method_ids()
    ctx = common.tryton_context(branch_id)
    # H3: a refund reverses the VAT of the original sale, not today's tax rules.
    fixed_taxes = {str(ln["product_id"]): list(ln["tax_ids"]) for ln in lines if ln.get("tax_ids") is not None}
    if fixed_taxes:
        ctx["motion_line_taxes"] = fixed_taxes
    with t.set_context(**ctx), t.transaction().set_user(cashier["tryton_user_id"] or 0):
        tlines = []
        for line in lines:
            product = Product(line["product_id"])
            unit_gross = Decimal(line["unit_gross"])
            if line.get("unit_net") is not None:
                unit_list = Decimal(line["unit_net"])
            else:
                taxes = list(product.customer_taxes_used)
                unit_list = Decimal(Tax.reverse_compute(unit_gross, taxes, sale_date)).quantize(Decimal("0.0001")) if taxes else unit_gross
            tlines.append(Line(product=product, quantity=float(line["quantity"]), unit_list_price=unit_list,
                               unit_gross_price=unit_gross))
        sale = Sale(company=ctx["company"], point=register["id"], date=sale_date,
                    employee=cashier.get("tryton_employee_id"), lines=tlines)
        sale.save()
        stored = []
        for line, tline in zip(lines, sale.lines):
            gross, tax = common.money(tline.gross_amount), common.money(tline.tax_amount)
            expected = line.get("amount", _line_amount(line["quantity"], Decimal(line["unit_gross"])))
            if gross != expected:  # one canonical calculator: API, till and Tryton must agree
                raise RuntimeError(f"line amount mismatch: Tryton {gross} vs expected {expected}")
            stored.append({"id": tline.id, "gross": gross, "tax": tax, "tax_ids": [x.id for x in tline.taxes]})
        total = common.money(sale.total)
        paid = sum((p["amount"] for p in payments), Decimal(0))
        if paid != total:
            raise Invalid(f"Payments ({paid}) do not equal the sale total ({total}).")
        created = Payment.create([{"sale": sale.id, "method": methods[p["method"]], "amount": p["amount"]}
                                  for p in payments if p["amount"]])
        if cash_session_id is not None:
            # H10: cash must land in the cash session of the cashier's verified open shift.
            for pay in created:
                if pay.session and pay.session.id != cash_session_id:
                    raise Conflict("The till's cash session changed (another shift was opened on this register). "
                                   "Close and reopen your shift.")
        Sale.process([sale])
        if sale.state != "done":
            raise Conflict("Sale could not be completed (amount still to pay).")
        if defer_post:  # seeding only: caller bulk-posts the batch with post_deferred()
            _DEFERRED.append(sale.id)
        else:
            Sale.post([sale])
            common.expect_state(sale, "posted", what="POS sale posting")
        number, total_tax = sale.number, common.money(sale.total_tax)
    discount_total = sum((_line_amount(abs(ln["quantity"]), Decimal(ln["list_gross"])) - _line_amount(abs(ln["quantity"]), Decimal(ln["unit_gross"]))
                          for ln in lines if "amount" not in ln), Decimal(0))
    unverified = any(p["method"] != "cash" for p in payments)
    row = t.sql_one("""
        INSERT INTO motion.pos_sale (idempotency_key, tryton_sale_id, number, branch_id, register_id, shift_id, cashier_id,
            source, edge_device_id, local_created_at, sale_date, total, total_tax, discount_total, kind, refund_of,
            approval_id, payments, unverified_claims)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING *""",
        (idempotency_key, sale.id, number, branch_id, register["id"], shift_id, cashier["id"], source, edge_device_id,
         local_created_at, sale_date, total, total_tax, discount_total, kind, refund_of, approval_id,
         json.dumps([{**p, "amount": str(p["amount"]),
                      "verification": "n/a" if p["method"] == "cash" else "pending"} for p in payments]),
         unverified))
    for line, st in zip(lines, stored):
        t.execute("""INSERT INTO motion.pos_sale_line (pos_sale_id, tryton_line_id, product_id, quantity, unit_price,
                     list_unit_price, discount_pct, refund_of_line, line_gross, line_tax, tax_ids)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (row["id"], st["id"], line["product_id"], line["quantity"], common.money(line["unit_gross"]),
                   common.money(line["list_gross"]), line.get("discount_pct") or 0, line.get("refund_of_line"),
                   st["gross"], st["tax"], st["tax_ids"]))
    if conflicts:
        for c in conflicts:
            t.execute("""INSERT INTO motion.discrepancy (kind, branch_id, source_type, source_id, product_id, quantity, detail)
                         VALUES ('sync_conflict', %s, 'pos_sale', %s, %s, %s, %s::jsonb)""",
                      (branch_id, str(row["id"]), c["product_id"], c["requested"] - c["available"],
                       json.dumps({"reason": "oversold while offline", **{k: str(v) for k, v in c.items()}})))
    return {**row, "replayed": False, "stock_conflicts": conflicts}


# ------------------------------------------------------------------ checkout

def _price_lines(raw_lines: list[dict], items: dict[int, dict]) -> list[dict]:
    lines = []
    for raw in raw_lines:
        pid = int(raw["product_id"])
        item = items.get(pid)
        if not item:
            raise Invalid("Product not available for sale.")
        q = common.qty(raw["quantity"])
        if q <= 0:
            raise Invalid("Quantities must be positive.")
        if not item["sold_by_weight"] and q != q.to_integral_value():
            raise Invalid(f"{item['name']} is sold per unit; use a whole quantity.")
        pct = Decimal(str(raw.get("discount_pct") or 0))
        if pct < 0 or pct > 100:
            raise Invalid("Discount must be between 0 and 100%.")
        gross = item["shelf_price"]
        unit = common.money(gross * (Decimal(100) - pct) / Decimal(100)) if pct else gross
        lines.append({"product_id": pid, "quantity": q, "unit_gross": unit, "list_gross": gross, "discount_pct": pct,
                      "name": item["name"], "uom": item["uom"]})
    return lines


def _check_override(actor: Principal, register: dict, lines: list[dict], approval_id: int | None) -> int | None:
    limit = Decimal(str(pos_policy().get("cashier_max_discount_pct", 0)))
    over = [ln for ln in lines if ln["discount_pct"] > limit]
    if not over:
        return None
    if not approval_id:
        raise Forbidden(f"Discounts above {limit}% need a manager override.", action="pos.discount.unauthorised",
                        branch_id=register["branch_id"])
    req = approvals.require_approved(approval_id, "price_override", branch_id=register["branch_id"])
    if req["consumed_at"]:
        raise Forbidden("This override was already used.", action="pos.override.reuse", target_type="approval",
                        target_id=req["ref"])
    if req["updated_at"] < utcnow() - timedelta(minutes=30):
        raise Forbidden("This override has expired; ask the manager again.", action="pos.override.expired",
                        target_type="approval", target_id=req["ref"])
    allowed = {(int(x["product_id"]), Decimal(str(x["discount_pct"]))) for x in req["payload"]["lines"]}
    if req["payload"].get("register_id") != register["id"] or req["payload"].get("cashier_id") != actor.user_id:
        raise Forbidden("Override was granted for a different till or cashier.", action="pos.override.mismatch")
    for ln in over:
        if (ln["product_id"], ln["discount_pct"]) not in allowed:
            raise Forbidden("Discount differs from what the manager approved.", action="pos.override.mismatch")
    return approval_id


def _sale_fingerprint(register_id, lines, payments, override_approval_id) -> dict:
    try:
        return {"register_id": int(register_id), "override_approval_id": override_approval_id,
                "lines": [{"product_id": int(x["product_id"]), "quantity": common.qty(x["quantity"]),
                           "discount_pct": Decimal(str(x.get("discount_pct") or 0))} for x in lines],
                "payments": [{"method": x.get("method"), "amount": common.money(x.get("amount")),
                              "reference": (x.get("reference") or "").strip() or None} for x in payments]}
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise Invalid("The basket or payment details are not valid.") from None


def checkout(actor: Principal, *, register_id: int, idempotency_key: str, lines: list[dict], payments: list[dict],
             override_approval_id: int | None = None, _sale_date=None, _defer_post: bool = False) -> dict:
    actor.require("pos.sell", action="pos.sale")
    request = _sale_fingerprint(register_id, lines, payments, override_approval_id)
    # H2: resolve the key before touching stock guards, approvals or the shift.
    replay = idempotency.claim("pos.sale", actor, idempotency_key, request)
    if replay:
        return receipt(actor, int(replay), replayed=True)
    reg = common.register(register_id)
    actor.require_branch(reg["branch_id"], action="pos.sale", target_type="register", target_id=register_id)
    shift = _hold_shift(actor, register_id, purpose="before selling")
    if not lines:
        raise Invalid("The basket is empty.")
    items = {i["id"]: i for i in catalog._rows(ids=[int(x["product_id"]) for x in lines])}
    priced = _price_lines(lines, items)
    override_id = _check_override(actor, reg, priced, override_approval_id)
    total = sum((_line_amount(ln["quantity"], ln["unit_gross"]) for ln in priced), Decimal(0))
    # Tender allocation: non-cash first (exact amounts), cash covers the rest; change from cash only.
    noncash = []
    cash_tendered = Decimal(0)
    for p in payments:
        method = p.get("method")
        if method not in METHOD_NAMES:
            raise Invalid("Unknown payment method.")
        amount = common.money(p.get("amount"))
        if amount <= 0:
            raise Invalid("Payment amounts must be positive.")
        if method == "cash":
            cash_tendered += amount
        else:
            if not (p.get("reference") or "").strip():
                raise Invalid("Enter the transaction reference for mobile money / card payments.")
            noncash.append({"method": method, "amount": amount, "reference": p["reference"].strip()[:60]})
    noncash_total = sum((p["amount"] for p in noncash), Decimal(0))
    if noncash_total > total:
        raise Invalid("Non-cash payments exceed the total.")
    cash_due = total - noncash_total
    if cash_tendered < cash_due:
        raise Invalid(f"Payment short by {cash_due - cash_tendered}.")
    change = cash_tendered - cash_due
    tryton_payments = noncash + ([{"method": "cash", "amount": cash_due, "tendered": str(cash_tendered),
                                   "change": str(change)}] if cash_due else [])
    cashier = {"id": actor.user_id, "tryton_user_id": actor.tryton_user_id, "tryton_employee_id": actor.tryton_employee_id}
    result = post_sale(register=reg, cashier=cashier, lines=priced, payments=tryton_payments, sale_date=_sale_date or common.today(),
                       idempotency_key=idempotency.internal_key("pos.sale", actor, idempotency_key), source="online",
                       shift_id=shift["id"], enforce_stock=not pos_policy().get("allow_negative_stock", False),
                       approval_id=override_id, defer_post=_defer_post, cash_session_id=shift["tryton_session_id"])
    idempotency.complete("pos.sale", actor, idempotency_key, request, result_type="pos_sale", result_id=result["id"])
    if override_id:
        approvals.mark_executed(override_id, actor=actor, result_type="pos_sale", result_id=result["id"])
    record(actor=actor, action="pos.sale", target_type="pos_sale", target_id=result["number"], branch_id=reg["branch_id"],
           amount=result["total"], after={"lines": len(priced), "discount_total": result["discount_total"],
                                          "payments": [p["method"] for p in tryton_payments], "override": override_id})
    return receipt(actor, result["id"])


def request_override(actor: Principal, *, register_id: int, lines: list[dict], manager_username: str,
                     manager_password: str, reason: str) -> dict:
    """In-person manager override at the till (the manager types their own credentials)."""
    actor.require("pos.sell", action="pos.override.request")
    reg = common.register(register_id)
    actor.require_branch(reg["branch_id"], action="pos.override.request")
    row = t.sql_one("SELECT * FROM motion.app_user WHERE lower(username)=%s", ((manager_username or "").strip().lower(),))
    if not row or row["status"] != "active" or not verify_password(row["password_hash"], manager_password or ""):
        raise Forbidden("Manager credentials not accepted.", action="pos.override.bad_credentials", branch_id=reg["branch_id"])
    manager = load_principal(row["id"], meta=actor.meta)
    if manager.user_id == actor.user_id:
        raise Forbidden("You cannot approve your own override.", action="pos.override.self")
    manager.require("pos.override.approve", action="pos.override.approve")
    manager.require_branch(reg["branch_id"], action="pos.override.approve")
    items = {i["id"]: i for i in catalog._rows(ids=[int(x["product_id"]) for x in lines])}
    priced = _price_lines(lines, items)
    amount = sum((_line_amount(ln["quantity"], ln["list_gross"] - ln["unit_gross"]) for ln in priced), Decimal(0))
    payload = {"register_id": reg["id"], "cashier_id": actor.user_id,
               "lines": [{"product_id": ln["product_id"], "discount_pct": str(ln["discount_pct"]), "name": ln["name"]} for ln in priced]}
    req = approvals.submit(actor, action_type="price_override", branch_id=reg["branch_id"], amount=amount,
                           title=f"Till discount on {reg['name']}", payload=payload, justification=reason)
    approved = approvals.act(manager, req["id"], decision="approve", comment=reason)
    if approved["status"] != "approved":
        raise Forbidden("Override policy needs more than one approver; use the approval inbox.", action="pos.override")
    return {"approval_id": req["id"], "ref": req["ref"], "approved_by": manager.full_name, "discount_amount": amount}


# ------------------------------------------------------------------ refunds

def _sale_row(sale_id: int) -> dict:
    row = t.sql_one("SELECT * FROM motion.pos_sale WHERE id=%s", (sale_id,))
    if not row:
        raise NotFound("Sale not found.")
    return row


def find_sale(actor: Principal, number: str) -> dict:
    actor.require_any("pos.refund.request", "pos.history.branch")
    row = t.sql_one("SELECT id FROM motion.pos_sale WHERE number=%s", ((number or "").strip(),))
    if not row:
        raise NotFound("No sale with that receipt number.")
    return receipt(actor, row["id"])


def _refund_amounts(orig: dict, q: Decimal) -> tuple[Decimal, Decimal]:
    """Gross and VAT to reverse for ``q`` units of an original line (Codex H3).

    Partial refunds take their proportional share (half-even); the refund that
    returns the last units closes the line's remaining gross and VAT exactly, so a
    series of partial refunds always reverses the original sale to the shilling."""
    left_q = common.qty(orig["quantity"]) - common.qty(orig["refunded_qty"])
    left_gross = common.money(orig["line_gross"] - orig["refunded_gross"])
    left_tax = common.money(orig["line_tax"] - orig["refunded_tax"])
    if q == left_q:
        return left_gross, left_tax
    share = q / common.qty(orig["quantity"])
    gross = min(common.line_amount(share, orig["line_gross"]), left_gross)
    tax = min(common.line_amount(share, orig["line_tax"]), left_tax, gross)
    return gross, tax


def _open_refund(sale_id: int) -> dict | None:
    return t.sql_one("""SELECT id, ref, status FROM motion.approval_request WHERE action_type='refund'
                        AND status IN ('pending','approved') AND consumed_at IS NULL
                        AND (payload->>'sale_id')::int = %s ORDER BY id LIMIT 1""", (sale_id,))


def _refund_plan(sale: dict, raw_lines: list[dict]) -> tuple[list[dict], Decimal]:
    """Validate requested refund lines against the original receipt (locked by the caller)."""
    originals = {r["id"]: r for r in t.sql("SELECT * FROM motion.pos_sale_line WHERE pos_sale_id=%s FOR UPDATE",
                                           (sale["id"],))}
    plan, seen, total = [], set(), Decimal(0)
    for ln in raw_lines:
        lid = int(ln["line_id"])
        if lid in seen:
            raise Invalid("Each receipt line can appear only once in a refund.")
        seen.add(lid)
        orig = originals.get(lid)
        if not orig or orig["quantity"] <= 0:
            raise Invalid("Line does not belong to this sale.")
        q = common.qty(ln["quantity"])
        left = common.qty(orig["quantity"]) - common.qty(orig["refunded_qty"])
        if q <= 0 or q > left:
            raise Invalid(f"Refund quantity exceeds what can still be refunded ({left}).")
        gross, tax = _refund_amounts(orig, q)
        total += gross
        plan.append({"line_id": lid, "quantity": str(q), "product_id": orig["product_id"], "gross": str(gross),
                     "tax": str(tax), "orig": orig})
    if not plan:
        raise Invalid("Select at least one line to refund.")
    return plan, total


def _refund_tenders(sale: dict, total: Decimal) -> list[dict]:
    """Till refunds are paid in cash (Tryton allows only cash pay-outs), and never more
    than the cash taken on the original receipt minus earlier cash refunds. A mobile-money
    or card portion is not turned into till cash: Finance refunds it through the provider
    (Codex H3; policy to be confirmed by Finance)."""
    cash_left = sum((Decimal(p["amount"]) for p in sale["payments"] if p["method"] == "cash"), Decimal(0))
    for r in t.sql("SELECT payments FROM motion.pos_sale WHERE refund_of=%s", (sale["id"],)):
        cash_left += sum((Decimal(p["amount"]) for p in r["payments"] if p["method"] == "cash"), Decimal(0))
    if total > cash_left:
        others = sorted({p["method"].replace("_", " ") for p in sale["payments"] if p["method"] != "cash"})
        raise Invalid(f"Only UGX {common.money(max(cash_left, Decimal(0))):,} of this receipt was paid in cash and can be "
                      f"refunded at the till. The {' / '.join(others) or 'non-cash'} part must be refunded by Finance "
                      "through the provider.")
    return [{"method": "cash", "amount": -common.money(total)}]


def request_refund(actor: Principal, *, sale_id: int, lines: list[dict], reason: str) -> dict:
    actor.require("pos.refund.request", action="pos.refund.request")
    sale = t.sql_one("SELECT * FROM motion.pos_sale WHERE id=%s FOR UPDATE", (sale_id,))  # one refund at a time per receipt
    if not sale:
        raise NotFound("Sale not found.")
    actor.require_branch(sale["branch_id"], action="pos.refund.request", target_type="pos_sale", target_id=sale["number"])
    if sale["kind"] != "sale":
        raise Invalid("Only sales can be refunded.")
    if not reason or len(reason.strip()) < 5:
        raise Invalid("Explain why the goods are being refunded.")
    other = _open_refund(sale_id)
    if other:
        raise Conflict(f"Refund {other['ref']} for this receipt is still open. Complete or cancel it first.")
    plan, amount = _refund_plan(sale, lines)
    _refund_tenders(sale, amount)  # refuse early what the till could not pay out
    return approvals.submit(actor, action_type="refund", branch_id=sale["branch_id"], amount=amount,
                            title=f"Refund on receipt {sale['number']}",
                            payload={"sale_id": sale_id, "number": sale["number"],
                                     "lines": [{k: v for k, v in ln.items() if k != "orig"} for ln in plan]},
                            justification=reason)


def execute_refund(actor: Principal, *, approval_id: int, register_id: int, idempotency_key: str,
                   _sale_date=None) -> dict:
    actor.require("pos.refund.execute", action="pos.refund.execute")
    request = {"approval_id": int(approval_id), "register_id": int(register_id)}
    replay = idempotency.claim("pos.refund", actor, idempotency_key, request)
    if replay:
        return receipt(actor, int(replay), replayed=True)
    reg = common.register(register_id)
    actor.require_branch(reg["branch_id"], action="pos.refund.execute")
    shift = _hold_shift(actor, register_id, purpose="to pay out the refund")
    req = approvals.require_approved(approval_id, "refund", branch_id=reg["branch_id"])
    sale = t.sql_one("SELECT * FROM motion.pos_sale WHERE id=%s FOR UPDATE", (int(req["payload"]["sale_id"]),))
    if not sale or sale["kind"] != "sale" or sale["branch_id"] != req["branch_id"]:
        raise Conflict("The approved refund no longer matches its original sale.")
    try:
        plan, total = _refund_plan(sale, req["payload"]["lines"])
    except Invalid:
        raise Conflict("Part of this refund has already been paid out; request a new refund.") from None
    # The amounts must be exactly what the manager approved (C1/H3).
    approved = {int(x["line_id"]): x for x in req["payload"]["lines"]}
    changed = common.money(total) != common.money(req["amount"])
    for ln in plan:
        a = approved[ln["line_id"]]
        if "gross" in a and (Decimal(a["gross"]), Decimal(a["tax"])) != (Decimal(ln["gross"]), Decimal(ln["tax"])):
            changed = True  # requests made before per-line amounts existed are checked on the total only
    if changed:
        raise Conflict("The refund amount no longer matches what was approved; request a new refund.")
    lines = []
    for ln in plan:
        orig, q, gross, tax = ln["orig"], Decimal(ln["quantity"]), Decimal(ln["gross"]), Decimal(ln["tax"])
        t.execute("""UPDATE motion.pos_sale_line SET refunded_qty = refunded_qty + %s, refunded_gross = refunded_gross + %s,
                     refunded_tax = refunded_tax + %s WHERE id=%s""", (q, gross, tax, orig["id"]))
        lines.append({"product_id": orig["product_id"], "quantity": -q,
                      "unit_gross": (gross / q).quantize(Decimal("0.0001")),
                      "unit_net": ((gross - tax) / q).quantize(Decimal("0.0001")),
                      "tax_ids": orig["tax_ids"], "amount": -gross,
                      "list_gross": Decimal(orig["unit_price"]), "discount_pct": 0, "refund_of_line": orig["id"]})
    cashier = {"id": actor.user_id, "tryton_user_id": actor.tryton_user_id, "tryton_employee_id": actor.tryton_employee_id}
    tenders = _refund_tenders(sale, total)
    result = post_sale(register=reg, cashier=cashier, lines=lines, payments=tenders,
                       sale_date=_sale_date or common.today(),
                       idempotency_key=idempotency.internal_key("pos.refund", actor, idempotency_key), source="online",
                       shift_id=shift["id"], enforce_stock=False, kind="refund", refund_of=sale["id"],
                       approval_id=approval_id, cash_session_id=shift["tryton_session_id"])
    approvals.mark_executed(approval_id, actor=actor, result_type="pos_sale", result_id=result["id"])
    idempotency.complete("pos.refund", actor, idempotency_key, request, result_type="pos_sale", result_id=result["id"])
    record(actor=actor, action="pos.refund.execute", target_type="pos_sale", target_id=result["number"],
           branch_id=reg["branch_id"], amount=total, reason=req["justification"],
           after={"original": sale["number"], "approval": req["ref"], "tenders": [p["method"] for p in tenders]})
    return receipt(actor, result["id"])


# ------------------------------------------------------------------ receipts & history

def receipt(actor: Principal, pos_sale_id: int, replayed: bool = False) -> dict:
    sale = _sale_row(pos_sale_id)
    if sale["cashier_id"] != actor.user_id:
        actor.require("pos.history.branch", action="pos.sale.view")
    actor.require_branch(sale["branch_id"], action="pos.sale.view", target_type="pos_sale", target_id=sale["number"])
    lines = t.sql("""SELECT l.*, pt.name, u.symbol AS uom FROM motion.pos_sale_line l
                     JOIN product_product pp ON pp.id=l.product_id JOIN product_template pt ON pt.id=pp.template
                     JOIN product_uom u ON u.id=pt.default_uom WHERE l.pos_sale_id=%s ORDER BY l.id""", (pos_sale_id,))
    for ln in lines:
        ln["amount"] = ln["line_gross"]
    meta = t.sql_one("""SELECT p.name AS register_name, b.name AS branch_name, u.full_name AS cashier_name,
                               ss.move, am.number AS journal_entry
                        FROM motion.pos_sale s JOIN sale_point p ON p.id=s.register_id JOIN motion_branch b ON b.id=s.branch_id
                        JOIN motion.app_user u ON u.id=s.cashier_id JOIN sale_point_sale ss ON ss.id=s.tryton_sale_id
                        LEFT JOIN account_move am ON am.id=ss.move WHERE s.id=%s""", (pos_sale_id,))
    settings = get_settings()
    return {**sale, **meta, "lines": lines, "replayed": replayed, "brand": settings.brand_name,
            "footer": pos_policy().get("receipt_footer"), "test_data": settings.demo_mode}


def list_sales(actor: Principal, *, branch_id: int | None, date_from, date_to, cashier_id: int | None = None,
               limit: int = 200) -> list[dict]:
    actor.require_any("pos.history.branch", "pos.history.own")
    if not actor.has("pos.history.branch"):
        cashier_id = actor.user_id
    if branch_id is not None:
        actor.require_branch(branch_id, action="pos.sales.list")
    allowed = actor.scoped_branch_ids(common.branch_ids())
    return t.sql("""SELECT s.id, s.number, s.kind, s.sale_date, s.posted_at, s.total, s.total_tax, s.discount_total,
                           s.source, s.unverified_claims, s.payments, b.code AS branch_code, p.name AS register_name,
                           u.full_name AS cashier_name
                    FROM motion.pos_sale s JOIN motion_branch b ON b.id=s.branch_id JOIN sale_point p ON p.id=s.register_id
                    JOIN motion.app_user u ON u.id=s.cashier_id
                    WHERE s.branch_id = ANY(%s) AND (%s::int IS NULL OR s.branch_id=%s) AND s.sale_date BETWEEN %s AND %s
                      AND (%s::bigint IS NULL OR s.cashier_id=%s)
                    ORDER BY s.posted_at DESC LIMIT %s""",
                 (allowed, branch_id, branch_id, date_from, date_to, cashier_id, cashier_id, limit))


def summarize_payload(obj: Any) -> str:
    return json.dumps(obj, default=str)[:500]
