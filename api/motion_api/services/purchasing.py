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
                           (SELECT COALESCE(sum(i.total_amount_cache),0) FROM account_invoice i
                             WHERE i.party=p.id AND i.type='in' AND i.state='posted') AS open_invoices_total
                    FROM party_party p WHERE p.id IN (SELECT DISTINCT preferred_supplier FROM motion.product_policy
                                                      WHERE preferred_supplier IS NOT NULL)
                       OR p.id IN (SELECT party FROM purchase_purchase)
                    ORDER BY p.name""")


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
    party = common.ensure_party(Party(int(p["supplier_id"])).name)
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
    rows = t.sql("""SELECT pu.id, pu.number, pu.reference, pu.state, pu.shipment_state, pu.invoice_state, pu.purchase_date,
                           pa.name AS supplier, b.id AS branch_id, b.code AS branch_code, pu.total_amount_cache AS total
                    FROM purchase_purchase pu JOIN party_party pa ON pa.id=pu.party JOIN motion_branch b ON b.warehouse=pu.warehouse
                    ORDER BY pu.id DESC LIMIT 200""")
    return [r for r in rows if actor.can_branch(r["branch_id"])]


def receive_goods(actor: Principal, purchase_id: int, *, lines: list[dict], note: str | None = None) -> dict:
    """Receive (fully or partly) against a PO. Shortfalls stay pending on the PO."""
    actor.require("stock.receive", action="stock.receive")
    po = po_detail(actor, purchase_id)
    actor.require_branch(po["branch_id"], action="stock.receive")
    Move = t.model("stock.move")
    ShipmentIn = t.model("stock.shipment.in")
    Purchase = t.model("purchase.purchase")
    by_line = {int(x["line_id"]): x for x in lines}
    pending = Move.search([("origin", "like", "purchase.line,%"), ("state", "=", "draft"),
                           ("purchase", "=", purchase_id)]) if "purchase" in Move._fields else []
    if not pending:
        pending = [m for m in Move.search([("state", "=", "draft"), ("shipment", "=", None)])
                   if m.origin and m.origin.__name__ == "purchase.line" and m.origin.purchase.id == purchase_id]
    if not pending:
        raise Conflict("Nothing is pending delivery on this order.")
    receive_moves, batches, shortages = [], [], []
    for m in pending:
        x = by_line.get(m.origin.id)
        if not x:
            continue
        got = common.qty(x["received_qty"])
        if got < 0 or got > common.qty(m.quantity):
            raise Invalid("Received quantity cannot exceed what is pending.")
        if got == 0:
            continue
        if got < common.qty(m.quantity):
            shortages.append({"product_id": m.product.id, "ordered": str(m.quantity), "received": str(got)})
            Move.write([m], {"quantity": float(got)})
        receive_moves.append(m)
        if x.get("expiry_date") or x.get("lot_ref"):
            batches.append((m.product.id, got, x.get("lot_ref"), x.get("expiry_date")))
    if not receive_moves:
        raise Invalid("Enter at least one received quantity.")
    with t.set_context(**common.tryton_context(po["branch_id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        shipment, = ShipmentIn.create([{"company": common.company_id(), "supplier": receive_moves[0].origin.purchase.party.id,
                                         "warehouse": po["warehouse"], "reference": po["number"],
                                         "warehouse_input": common.branch(po["branch_id"])["input_location_id"],
                                         "warehouse_storage": common.branch(po["branch_id"])["storage_location_id"],
                                         "incoming_moves": [("add", [m.id for m in receive_moves])]}])
        ShipmentIn.receive([shipment])
        ShipmentIn.do([shipment])
        common.expect_state(shipment, "done", what="supplier receipt")
        Purchase.process([Purchase(purchase_id)])
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
                           (SELECT COALESCE(sum(l.credit - l.debit), 0) FROM account_move_line l
                              WHERE l.move = i.move AND l.account = i.account AND l.reconciliation IS NULL) AS open_amount,
                           i.payment_term_date
                    FROM account_invoice i JOIN party_party pa ON pa.id=i.party LEFT JOIN motion_branch b ON b.id=i.branch
                    WHERE i.type='in' AND (%s::text IS NULL OR i.state=%s) ORDER BY i.id DESC LIMIT 300""", (state, state))
    out = []
    for r in rows:
        # H1: unallocated bills are visible to head-office (all-branch) users only.
        if (r["branch_id"] is None and not actor.all_branches) or (r["branch_id"] and not actor.can_branch(r["branch_id"])):
            continue
        r["total"] = common.money(r["total"]) if r["total"] is not None else None
        if r["state"] == "draft":
            r["open_amount"] = None
        out.append(r)
    return out


def invoice_detail(actor: Principal, invoice_id: int) -> dict:
    actor.require("finance.view")
    Invoice = t.model("account.invoice")
    inv = Invoice(invoice_id)
    if inv.type != "in":
        raise NotFound("Supplier invoice not found.")
    if (inv.branch and not actor.can_branch(inv.branch.id)) or (not inv.branch and not actor.all_branches):
        raise Forbidden("Invoice belongs to another branch.", action="invoice.view", target_type="invoice", target_id=str(invoice_id))
    lines = []
    for ln in inv.lines:
        received = None
        if ln.origin and ln.origin.__name__ == "purchase.line":
            pl = ln.origin
            received = sum((m.quantity for m in pl.moves if m.state == "done"), 0.0)
            ordered = pl.quantity
            po_price = pl.unit_price
        else:
            ordered = po_price = None
        lines.append({"description": ln.description or (ln.product.rec_name if ln.product else ""), "quantity": ln.quantity,
                      "unit_price": ln.unit_price, "amount": ln.amount, "account": ln.account.rec_name if ln.account else None,
                      "ordered": ordered, "received": received, "po_unit_price": po_price,
                      "match": None if received is None else (abs(received - ln.quantity) < 1e-6 and ln.unit_price == po_price)})
    return {"id": inv.id, "number": inv.number, "state": inv.state, "supplier": inv.party.rec_name, "reference": inv.reference,
            "invoice_date": inv.invoice_date, "untaxed": inv.untaxed_amount, "tax": inv.tax_amount, "total": inv.total_amount,
            "amount_to_pay": inv.amount_to_pay if inv.state in {"posted", "paid"} else None,
            "branch": inv.branch.name if inv.branch else None, "lines": lines,
            "three_way_match": all(x["match"] for x in lines if x["match"] is not None)}


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
    if not supplier_reference.strip():
        raise Invalid("Enter the supplier's invoice number.")
    detail = invoice_detail(actor, invoice_id)
    if not detail["three_way_match"]:
        raise Conflict("Invoice does not match the order/receipt (quantity or price). Correct it before posting.")
    with t.set_context(**common.tryton_context(inv.branch.id if inv.branch else None)), \
            t.transaction().set_user(actor.tryton_user_id or 0):
        Invoice.write([inv], {"reference": supplier_reference.strip(), "invoice_date": invoice_date,
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
    if amount > common.money(inv.amount_to_pay):
        raise Conflict("Invoice balance changed; the approved amount now exceeds what is owed.")
    pm = Method.search([("name", "=", method)], limit=1)
    if not pm:
        raise Invalid("Unknown payment method.")
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
