"""Procurement, supplier invoices, expenses and supplier payments.

Separation of duties (each a distinct, audited event):
  requisition approval  ->  purchase order issued (Tryton purchase.purchase)
  goods received        ->  Tryton stock.shipment.in (stock valued at PO cost)
  supplier invoice      ->  drafted by Tryton from received quantities; the
                            accountant verifies the 3-way match and posts it
  payment request       ->  separate approval chain
  payment recorded      ->  Tryton pay_invoice + reconciliation

Expenses follow the same invoice/payment path (accrual: the expense is
recognised when the approved bill is posted, not when it is paid).
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from .. import tryton as t
from ..audit import record
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..security import Principal
from ..setup_base import EXPENSE_CATEGORIES
from . import approvals, catalog, common


def suppliers(actor: Principal) -> list[dict]:
    actor.require_any("procurement.request", "finance.view", "catalog.view")
    return t.sql("""SELECT p.id, p.name,
                           CASE WHEN %s THEN (SELECT COALESCE(sum(i.total_amount_cache),0) FROM account_invoice i
                             WHERE i.party=p.id AND i.type='in' AND i.state='posted'
                               AND (%s OR i.branch=ANY(%s))) END AS open_invoices_total
                    FROM party_party p WHERE p.id IN (SELECT DISTINCT preferred_supplier FROM motion.product_policy
                                                      WHERE preferred_supplier IS NOT NULL)
                       OR p.id IN (SELECT party FROM purchase_purchase)
                    ORDER BY p.name, p.id""", (actor.has("finance.view"), actor.all_branches, sorted(actor.branch_ids)))


def _velocity(branch_id: int, product_ids: list[int], days: int = 14) -> dict[int, Decimal]:
    rows = t.sql("""SELECT l.product_id, sum(l.quantity) AS q FROM motion.pos_sale_line l
                    JOIN motion.pos_sale s ON s.id=l.pos_sale_id
                    WHERE s.branch_id=%s AND s.sale_date > %s AND l.product_id = ANY(%s) GROUP BY 1""",
                 (branch_id, common.today() - dt.timedelta(days=days), product_ids))
    return {r["product_id"]: common.qty(Decimal(r["q"]) / days) for r in rows}


def requisition_evidence(branch_id: int, product_ids: list[int], actor: Principal | None = None) -> list[dict]:
    """Server-computed evidence shown to approvers (never trusted from the client).

    H1: other branches' stock is included only for branches the requester may see."""
    branches = [b for b in common.branches() if actor is None or b["id"] == branch_id or actor.can_branch(b["id"])]
    on_hand = common.stock_on_hand([b["storage_location_id"] for b in branches], product_ids)
    vel = _velocity(branch_id, product_ids)
    names = common.product_names(set(product_ids))
    here = common.branch(branch_id)
    out = []
    for pid in product_ids:
        stock_here = on_hand.get((here["storage_location_id"], pid), Decimal(0))
        daily = vel.get(pid, Decimal(0))
        elsewhere = {b["code"]: on_hand.get((b["storage_location_id"], pid), Decimal(0)) for b in branches if b["id"] != branch_id}
        out.append({"product_id": pid, "product": names[pid]["name"], "on_hand": str(stock_here),
                    "daily_sales_14d": str(daily),
                    "days_of_cover": str((stock_here / daily).quantize(Decimal("0.1"))) if daily else None,
                    "stock_at_other_branches": {k: str(v) for k, v in elsewhere.items()}})
    return out


def submit_requisition(actor: Principal, *, branch_id: int, supplier_id: int, lines: list[dict], justification: str,
                       urgency: str = "normal") -> dict:
    actor.require("procurement.request", action="procurement.request")
    actor.require_branch(branch_id, action="procurement.request")
    if not lines:
        raise Invalid("Add at least one item.")
    if not justification or len(justification.strip()) < 5:
        raise Invalid("Give a justification.")
    if not t.sql_one("SELECT 1 AS x FROM party_party WHERE id=%s", (supplier_id,)):
        raise Invalid("Unknown supplier.")
    pids = [int(ln["product_id"]) for ln in lines]
    items = {r["id"]: r for r in catalog._rows(ids=pids)}
    clean, amount = [], Decimal(0)
    for ln in lines:
        pid = int(ln["product_id"])
        if pid not in items:
            raise Invalid("Unknown product.")
        q = common.qty(ln["quantity"])
        cost = Decimal(str(ln.get("unit_cost") or items[pid]["cost_price"])).quantize(Decimal("0.01"))
        if q <= 0 or cost < 0:
            raise Invalid("Quantities and costs must be positive.")
        amount += q * cost
        clean.append({"product_id": pid, "product": items[pid]["name"], "quantity": str(q), "unit_cost": str(cost),
                      "uom": items[pid]["uom"]})
    supplier = t.sql_one("SELECT name FROM party_party WHERE id=%s", (supplier_id,))["name"]
    payload = {"supplier_id": supplier_id, "supplier": supplier, "lines": clean,
               "evidence": requisition_evidence(branch_id, pids, actor)}
    return approvals.submit(actor, action_type="purchase_request", branch_id=branch_id, amount=amount,
                            title=f"Restock from {supplier} ({len(clean)} items)", payload=payload,
                            justification=justification, urgency=urgency)


def issue_po(actor: Principal, request_id: int) -> dict:
    actor.require("procurement.issue_po", action="procurement.issue_po")
    req = approvals.require_approved(request_id, "purchase_request")
    actor.require_branch(req["branch_id"], action="procurement.issue_po")
    if req["result_id"]:
        raise Conflict("A purchase order was already issued.")
    p = req["payload"]
    b = common.branch(req["branch_id"])
    Purchase = t.model("purchase.purchase")
    uoms = {r["id"]: r["default_uom"] for r in t.sql(
        "SELECT pp.id, pt.default_uom FROM product_product pp JOIN product_template pt ON pt.id=pp.template WHERE pp.id = ANY(%s)",
        ([int(x["product_id"]) for x in p["lines"]],))}
    Party = t.model("party.party")
    party = Party(int(p["supplier_id"]))
    if not party.active or not party.address_get(type="invoice"):
        raise Conflict("The approved supplier must be active and have an invoice address.")
    with t.set_context(**common.tryton_context(b["id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        purchase, = Purchase.create([{
            "company": common.company_id(), "party": party.id, "warehouse": b["warehouse"],
            "invoice_address": party.address_get(type="invoice").id,
            "invoice_method": "fulfillment", "reference": req["ref"], "description": req["title"],
            "purchase_date": common.today(),
            "lines": [("create", [{"type": "line", "product": int(x["product_id"]), "quantity": float(x["quantity"]),
                                   "unit": uoms[int(x["product_id"])], "unit_price": Decimal(x["unit_cost"]),
                                   "description": x["product"]} for x in p["lines"]])]}])
        Purchase.quote([purchase])
        Purchase.confirm([purchase])
        Purchase.process([purchase])
        common.expect_state(purchase, "processing", "confirmed", what="purchase order")
        number = purchase.number
    approvals.mark_executed(request_id, actor=actor, result_type="purchase.purchase", result_id=purchase.id,
                            fulfilment_status="po_issued", final=False)
    record(actor=actor, action="procurement.issue_po", target_type="approval", target_id=req["ref"],
           branch_id=b["id"], amount=req["amount"], after={"purchase": number, "supplier": p["supplier"]})
    return po_detail(actor, purchase.id)


def po_detail(actor: Principal, purchase_id: int) -> dict:
    actor.require_any("procurement.request", "stock.receive", "finance.view")
    po = t.sql_one("""SELECT pu.id, pu.number, pu.reference, pu.state, pu.shipment_state, pu.invoice_state, pu.purchase_date,
                             pa.name AS supplier, pu.warehouse, b.id AS branch_id, b.name AS branch_name,
                             pu.total_amount_cache AS total
                      FROM purchase_purchase pu JOIN party_party pa ON pa.id=pu.party
                      JOIN motion_branch b ON b.warehouse=pu.warehouse WHERE pu.id=%s""", (purchase_id,))
    if not po:
        raise NotFound("Purchase order not found.")
    actor.require_branch(po["branch_id"], action="procurement.view")
    po["lines"] = t.sql("""SELECT pl.id, pl.product, pl.description, pl.quantity, pl.unit_price,
                                  COALESCE((SELECT sum(m.quantity) FROM stock_move m WHERE m.origin='purchase.line,'||pl.id AND m.state='done'),0) AS received,
                                  COALESCE((SELECT sum(m.quantity) FROM stock_move m WHERE m.origin='purchase.line,'||pl.id AND m.state NOT IN ('done','cancelled')),0) AS pending
                           FROM purchase_line pl WHERE pl.purchase=%s ORDER BY pl.id""", (purchase_id,))
    po["invoices"] = t.sql("""SELECT DISTINCT i.id, i.number, i.state, i.total_amount_cache AS total, i.reference
                              FROM account_invoice i JOIN account_invoice_line il ON il.invoice=i.id
                              JOIN purchase_line pl ON il.origin='purchase.line,'||pl.id WHERE pl.purchase=%s""", (purchase_id,))
    req = t.sql_one("SELECT id, ref FROM motion.approval_request WHERE result_type='purchase.purchase' AND result_id=%s",
                    (str(purchase_id),))
    po["request"] = req
    return po


def open_purchase_orders(actor: Principal) -> list[dict]:
    actor.require_any("procurement.request", "stock.receive", "finance.view")
    return t.sql("""SELECT pu.id, pu.number, pu.reference, pu.state, pu.shipment_state, pu.invoice_state, pu.purchase_date,
                           pa.name AS supplier, b.id AS branch_id, b.code AS branch_code, pu.total_amount_cache AS total
                    FROM purchase_purchase pu JOIN party_party pa ON pa.id=pu.party JOIN motion_branch b ON b.warehouse=pu.warehouse
                    WHERE (%s OR b.id=ANY(%s))
                    ORDER BY pu.id DESC LIMIT 200""", (actor.all_branches, sorted(actor.branch_ids)))


def receive_goods(actor: Principal, purchase_id: int, *, lines: list[dict], note: str | None = None) -> dict:
    """Receive (fully or partly) against a PO. Shortfalls stay pending on the PO."""
    actor.require("stock.receive", action="stock.receive")
    po = po_detail(actor, purchase_id)
    actor.require_branch(po["branch_id"], action="stock.receive")
    Move = t.model("stock.move")
    ShipmentIn = t.model("stock.shipment.in")
    Purchase = t.model("purchase.purchase")
    purchase = Purchase(purchase_id)
    by_line = {int(x["line_id"]): x for x in lines}
    if len(by_line) != len(lines) or not by_line.keys() <= {line.id for line in purchase.lines}:
        raise Invalid("List each purchase line once and use lines from this order.")
    receive_moves, adjustments, batches, shortages = [], [], [], []
    for line in purchase.lines:
        if line.id not in by_line:
            continue
        x = by_line[line.id]
        pending = sorted((move for move in line.moves if move.state == "draft" and not move.shipment
                          and move.from_location.type == "supplier"), key=lambda move: move.id)
        got = common.qty(x["received_qty"])
        total_pending = sum((common.qty(move.quantity) for move in pending), Decimal(0))
        if got < 0 or got > total_pending:
            raise Invalid("Received quantity cannot exceed what is pending.")
        if got == 0:
            continue
        if got < total_pending:
            shortages.append({"product_id": line.product.id, "ordered": str(total_pending), "received": str(got)})
        if x.get("expiry_date") or x.get("lot_ref"):
            batches.append((line.product.id, got, x.get("lot_ref"), x.get("expiry_date")))
        remaining = got
        for move in pending:
            if not remaining:
                break
            portion = min(remaining, common.qty(move.quantity))
            if portion != common.qty(move.quantity):
                adjustments.append((move, portion))
            receive_moves.append(move)
            remaining -= portion
    if not receive_moves:
        raise Invalid("Enter at least one received quantity.")
    with t.set_context(**common.tryton_context(po["branch_id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        for move, portion in adjustments:
            Move.write([move], {"quantity": float(portion)})
        shipment, = ShipmentIn.create([{"company": common.company_id(), "supplier": purchase.party.id,
                                         "warehouse": po["warehouse"], "reference": po["number"],
                                         "warehouse_input": common.branch(po["branch_id"])["input_location_id"],
                                         "warehouse_storage": common.branch(po["branch_id"])["storage_location_id"],
                                         "incoming_moves": [("add", [m.id for m in receive_moves])]}])
        ShipmentIn.receive([shipment])
        ShipmentIn.do([shipment])
        common.expect_state(shipment, "done", what="supplier receipt")
        Purchase.process([purchase])
    for pid, q, lot, expiry in batches:
        t.execute("""INSERT INTO motion.stock_batch (product_id, branch_id, lot_ref, expiry_date, received_qty, source_type,
                     source_id, created_by) VALUES (%s,%s,%s,%s,%s,'purchase_receipt',%s,%s)""",
                  (pid, po["branch_id"], lot, expiry, q, shipment.number, actor.user_id))
    for s in shortages:
        t.execute("""INSERT INTO motion.discrepancy (kind, branch_id, source_type, source_id, product_id, quantity, detail,
                     created_by, status, resolution) VALUES ('receipt_shortage',%s,'purchase',%s,%s,%s,%s::jsonb,%s,'open',NULL)""",
                  (po["branch_id"], str(purchase_id), s["product_id"], Decimal(s["ordered"]) - Decimal(s["received"]),
                   json.dumps({**s, "note": note, "po": po["number"], "remaining": "still pending on PO"}), actor.user_id))
    after = po_detail(actor, purchase_id)
    if after["request"]:
        complete = all(Decimal(ln["pending"]) == 0 for ln in after["lines"])
        approvals.set_fulfilment(after["request"]["id"], "received" if complete else "partially_received")
    record(actor=actor, action="stock.receive", target_type="purchase", target_id=po["number"], branch_id=po["branch_id"],
           reason=note, after={"shipment": shipment.number, "lines": len(receive_moves), "shortages": shortages})
    return after


# ------------------------------------------------------------------ invoices / payables

def payables(actor: Principal, *, state: str | None = None) -> list[dict]:
    actor.require("finance.view")
    rows = t.sql("""SELECT i.id, i.number, i.reference, i.state, i.invoice_date, i.total_amount_cache AS total,
                           i.description, pa.name AS supplier, i.branch AS branch_id, b.code AS branch_code,
                           i.payment_term_date
                    FROM account_invoice i JOIN party_party pa ON pa.id=i.party LEFT JOIN motion_branch b ON b.id=i.branch
                    WHERE i.type='in' AND (%s::text IS NULL OR i.state=%s)
                      AND (%s OR i.branch=ANY(%s))
                    ORDER BY i.id DESC LIMIT 300""", (state, state, actor.all_branches, sorted(actor.branch_ids)))
    Invoice = t.model("account.invoice")
    outstanding = Invoice.get_amount_to_pay(Invoice.browse([r["id"] for r in rows if r["state"] == "posted"]), "amount_to_pay")
    for r in rows:
        r["total"] = common.money(r["total"]) if r["total"] is not None else None
        r["open_amount"] = None if r["state"] in {"draft", "validated"} else outstanding.get(r["id"], Decimal(0))
    return rows


def _invoice_matches(inv) -> dict[int, dict]:
    """Match goods bills against their fulfillment moves and unbilled balances."""
    Uom = t.model("product.uom")
    billed_in_checked_invoice: dict[int, Decimal] = {}
    matches = {}
    for line in inv.lines:
        if line.type != "line" or not line.origin or line.origin.__name__ != "purchase.line":
            continue
        po_line = line.origin
        receipt_moves = {move for move in po_line.moves if move.state == "done"
                         and move.from_location.type == "supplier" and move.to_location.type != "supplier"}
        linked = set(line.stock_moves)
        valid_receipts = linked & receipt_moves
        received = sum((common.qty(Uom.compute_qty(move.unit, move.quantity, po_line.unit))
                        for move in valid_receipts), Decimal(0))
        billed = sum((common.qty(Uom.compute_qty(other.unit or po_line.unit, other.quantity, po_line.unit))
                      for other in po_line.invoice_lines if other.type == "line" and other.invoice
                      and other.invoice.id != inv.id and other.invoice.state in {"posted", "paid"}), Decimal(0))
        all_received = sum((common.qty(Uom.compute_qty(move.unit, move.quantity, po_line.unit))
                            for move in receipt_moves), Decimal(0))
        qty = common.qty(Uom.compute_qty(line.unit or po_line.unit, line.quantity, po_line.unit))
        current = billed_in_checked_invoice.get(po_line.id, Decimal(0)) + qty
        billed_in_checked_invoice[po_line.id] = current
        matched = (bool(linked) and linked == valid_receipts and qty > 0 and qty <= received
                   and (inv.state in {"posted", "paid"} or billed + current <= all_received)
                   and line.product == po_line.product and line.unit == po_line.unit
                   and line.unit_price == po_line.unit_price and inv.party == po_line.purchase.party)
        matches[line.id] = {"ordered": po_line.quantity, "received": received,
                            "po_unit_price": po_line.unit_price, "match": matched}
    return matches


def invoice_detail(actor: Principal, invoice_id: int) -> dict:
    actor.require("finance.view")
    Invoice = t.model("account.invoice")
    inv = Invoice(invoice_id)
    if inv.type != "in":
        raise NotFound("Supplier invoice not found.")
    if (inv.branch and not actor.can_branch(inv.branch.id)) or (not inv.branch and not actor.all_branches):
        raise Forbidden("Invoice belongs to another branch.", action="invoice.view", target_type="invoice", target_id=str(invoice_id))
    matched = _invoice_matches(inv)
    lines = []
    for ln in inv.lines:
        if ln.type != "line":
            continue
        lines.append({"description": ln.description or (ln.product.rec_name if ln.product else ""), "quantity": ln.quantity,
                      "unit_price": ln.unit_price, "amount": ln.amount, "account": ln.account.rec_name if ln.account else None,
                      **matched.get(ln.id, {"ordered": None, "received": None, "po_unit_price": None, "match": None})})
    return {"id": inv.id, "number": inv.number, "state": inv.state, "supplier": inv.party.rec_name, "reference": inv.reference,
            "invoice_date": inv.invoice_date, "untaxed": inv.untaxed_amount, "tax": inv.tax_amount, "total": inv.total_amount,
            "amount_to_pay": inv.amount_to_pay if inv.state in {"posted", "paid"} else None,
            "branch": inv.branch.name if inv.branch else None, "lines": lines,
            "three_way_match": bool(lines) and all(x["match"] is True for x in lines)}


def _claim_supplier_reference(inv, reference: str) -> None:
    scope = (inv.company.id, inv.party.id, reference)
    if t.sql_one("""SELECT id FROM account_invoice WHERE company=%s AND party=%s
                    AND lower(btrim(reference))=lower(%s) AND type='in' AND state IN ('posted','paid') AND id<>%s
                    LIMIT 1""", (*scope, inv.id)):
        raise Conflict("This supplier invoice number has already been posted.")
    if not t.execute("""INSERT INTO motion.supplier_invoice_ref (company_id, supplier_id, reference, invoice_id)
                        VALUES (%s,%s,lower(%s),%s) ON CONFLICT DO NOTHING""", (*scope, inv.id)):
        raise Conflict("This supplier invoice number has already been posted.")


def post_invoice(actor: Principal, invoice_id: int, *, supplier_reference: str, invoice_date: dt.date) -> dict:
    actor.require("invoice.post", action="invoice.post")
    Invoice = t.model("account.invoice")
    inv = Invoice(invoice_id)
    if not inv.branch:
        if not actor.all_branches:
            raise Forbidden("Unallocated bills can only be posted by head office.", action="invoice.post")
    else:
        actor.require_branch(inv.branch.id, action="invoice.post", target_type="invoice", target_id=invoice_id)
    if inv.type != "in" or inv.state != "draft":
        raise Conflict("Only draft supplier invoices can be posted.")
    reference = supplier_reference.strip()
    if not reference:
        raise Invalid("Enter the supplier's invoice number.")
    purchases = {ln.origin.purchase.id for ln in inv.lines if ln.origin and ln.origin.__name__ == "purchase.line"}
    for purchase_id in sorted(purchases):
        t.execute("""INSERT INTO motion.purchase_billing_guard (purchase_id) VALUES (%s)
                     ON CONFLICT (purchase_id) DO UPDATE SET version=motion.purchase_billing_guard.version+1""",
                  (purchase_id,))
    detail = invoice_detail(actor, invoice_id)
    if not detail["three_way_match"]:
        raise Conflict("Invoice does not match the order/receipt (quantity or price). Correct it before posting.")
    _claim_supplier_reference(inv, reference)
    with t.set_context(**common.tryton_context(inv.branch.id if inv.branch else None)), \
            t.transaction().set_user(actor.tryton_user_id or 0):
        Invoice.write([inv], {"reference": reference, "invoice_date": invoice_date,
                              "accounting_date": invoice_date})
        Invoice.validate_invoice([inv])
        Invoice.post([inv])
        common.expect_state(inv, "posted", "paid", what="supplier invoice")
    record(actor=actor, action="invoice.post", target_type="invoice", target_id=inv.number,
           branch_id=inv.branch.id if inv.branch else None, amount=inv.total_amount,
           after={"supplier": inv.party.rec_name, "reference": supplier_reference})
    return invoice_detail(actor, invoice_id)


def request_payment(actor: Principal, invoice_id: int, *, amount: Decimal, reason: str) -> dict:
    actor.require("payment.request", action="payment.request")
    Invoice = t.model("account.invoice")
    inv = Invoice(invoice_id)
    # H1: scope first, before revealing state or balance.
    if not inv.branch:
        raise Conflict("This bill is not allocated to a branch; allocate it before requesting payment.")
    actor.require_branch(inv.branch.id, action="payment.request", target_type="invoice", target_id=invoice_id)
    if inv.type != "in" or inv.state != "posted":
        raise Conflict("Only posted, unpaid supplier invoices can be paid.")
    amount = common.money(amount)
    if amount <= 0 or amount > common.money(inv.amount_to_pay):
        raise Invalid(f"Amount must be between 1 and {common.money(inv.amount_to_pay)}.")
    pending = t.sql_one("""SELECT 1 AS x FROM motion.approval_request WHERE action_type='supplier_payment'
                           AND status IN ('pending','approved') AND (payload->>'invoice_id')::int=%s""", (invoice_id,))
    if pending:
        raise Conflict("A payment for this invoice is already awaiting approval or execution.")
    branch_id = inv.branch.id
    return approvals.submit(actor, action_type="supplier_payment", branch_id=branch_id, amount=amount,
                            title=f"Pay {inv.party.rec_name} - {inv.number}",
                            payload={"invoice_id": invoice_id, "invoice": inv.number, "supplier": inv.party.rec_name,
                                     "amount": str(amount)}, justification=reason)


def record_payment(actor: Principal, request_id: int, *, method: str = "Bank transfer (TEST bank)",
                   paid_on: dt.date | None = None) -> dict:
    actor.require("payment.record", action="payment.record")
    # The requester may record the payment: the maker-checker control is the
    # approval chain, which the requester could not act on.
    req = approvals.require_approved(request_id, "supplier_payment")
    actor.require_branch(req["branch_id"], action="payment.record", target_type="approval", target_id=req["ref"])
    Invoice = t.model("account.invoice")
    Method = t.model("account.invoice.payment.method")
    MoveLine = t.model("account.move.line")
    inv = Invoice(int(req["payload"]["invoice_id"]))
    amount = Decimal(req["payload"]["amount"])
    if inv.type != "in" or inv.state != "posted" or not inv.branch or inv.branch.id != req["branch_id"]:
        raise Conflict("The approved bill is no longer payable under this branch.")
    if amount <= 0 or amount != req["amount"]:
        raise Conflict("The payment amount no longer matches the approval.")
    if amount > common.money(inv.amount_to_pay):
        raise Conflict("Invoice balance changed; the approved amount now exceeds what is owed.")
    pm = Method.search([("name", "=", method)], limit=1)
    if not pm:
        raise Invalid("Unknown payment method.")
    if pm[0].credit_account and pm[0].credit_account.code == "1.1.1.02":
        common.lock_safe(req["branch_id"])
    with t.set_context(**common.tryton_context(req["branch_id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        lines = inv.pay_invoice(amount, pm[0], paid_on or common.today(), f"{req['ref']} {inv.number}")
        inv = Invoice(inv.id)
        if not inv.amount_to_pay:
            to_reconcile = [ln for ln in inv.lines_to_pay if not ln.reconciliation] + \
                           [ln for ln in inv.payment_lines if not ln.reconciliation]
            if to_reconcile:
                MoveLine.reconcile(to_reconcile)
        Invoice.process([inv])
    approvals.mark_executed(request_id, actor=actor, result_type="account.invoice", result_id=inv.id,
                            fulfilment_status="paid")
    record(actor=actor, action="payment.record", target_type="invoice", target_id=inv.number, branch_id=req["branch_id"],
           amount=amount, after={"method": method, "approval": req["ref"], "lines": [ln.id for ln in lines]})
    return invoice_detail(actor, inv.id)


# ------------------------------------------------------------------ expenses

def submit_expense(actor: Principal, *, branch_id: int, category: str, payee: str, amount: Decimal, description: str,
                   document_ref: str | None, incurred_on: dt.date) -> dict:
    actor.require("expense.submit", action="expense.submit")
    actor.require_branch(branch_id, action="expense.submit")
    if category not in EXPENSE_CATEGORIES:
        raise Invalid("Unknown expense category.")
    amount = common.money(amount)
    if amount <= 0:
        raise Invalid("Amount must be positive.")
    if not payee.strip() or not description.strip():
        raise Invalid("Payee and description are required.")
    return approvals.submit(actor, action_type="expense", branch_id=branch_id, amount=amount,
                            title=f"{category.replace('_', ' ').title()}: {payee.strip()}",
                            payload={"category": category, "payee": payee.strip(), "description": description.strip(),
                                     "document_ref": document_ref, "incurred_on": incurred_on.isoformat(),
                                     "account": EXPENSE_CATEGORIES[category]}, justification=description)


def post_expense(actor: Principal, request_id: int, *, posting_date: dt.date | None = None) -> dict:
    """Turn an approved expense into a posted supplier bill (accrual)."""
    actor.require("invoice.post", action="expense.post")
    req = approvals.require_approved(request_id, "expense")
    actor.require_branch(req["branch_id"], action="expense.post", target_type="approval", target_id=req["ref"])
    if req["initiator_id"] == actor.user_id:
        raise Forbidden("You cannot post an expense you submitted yourself.", action="expense.post.self",
                        target_type="approval", target_id=req["ref"])
    p = req["payload"]
    Invoice = t.model("account.invoice")
    party = common.ensure_party(p["payee"])
    account = common.account_by_code(p["account"])
    payable = common.account_by_code("2.1.1.01")
    when = posting_date or dt.date.fromisoformat(p["incurred_on"])
    with t.set_context(**common.tryton_context(req["branch_id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        inv, = Invoice.create([{"type": "in", "company": common.company_id(), "party": party.id, "account": payable.id,
                                "invoice_address": party.address_get(type="invoice").id,
                                "journal": t.model("ir.model.data").get_id("account", "journal_expense"),
                                "invoice_date": when, "accounting_date": when, "reference": p.get("document_ref") or req["ref"],
                                "description": p["description"], "branch": req["branch_id"],
                                "currency": t.sql_one("SELECT currency FROM company_company LIMIT 1")["currency"],
                                "lines": [("create", [{"type": "line", "account": account.id, "quantity": 1,
                                                       "unit_price": Decimal(req["amount"]), "description": p["description"]}])]}])
        Invoice.validate_invoice([inv])
        Invoice.post([inv])
        common.expect_state(inv, "posted", what="expense bill")
    approvals.mark_executed(request_id, actor=actor, result_type="account.invoice", result_id=inv.id,
                            fulfilment_status="posted", final=True)
    record(actor=actor, action="expense.post", target_type="invoice", target_id=inv.number, branch_id=req["branch_id"],
           amount=req["amount"], after={"category": p["category"], "payee": p["payee"], "approval": req["ref"]})
    return invoice_detail(actor, inv.id)
