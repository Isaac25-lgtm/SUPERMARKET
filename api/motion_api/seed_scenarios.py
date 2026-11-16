"""TEST DATA operational scenarios, executed through the real services and
approval chains (each scenario is its own resumable phase)."""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from decimal import Decimal

from . import tryton as t
from .seed import _mark, _phase_done, principal
from .services import approvals, catalog, common, pos, purchasing, stock

logger = logging.getLogger("motion.seed")
D = Decimal


def _pid(name_prefix: str) -> int:
    return t.sql_one("""SELECT pp.id FROM product_product pp JOIN product_template pt ON pt.id=pp.template
                        WHERE pt.name LIKE %s LIMIT 1""", (name_prefix + "%",))["id"]


def _bid(code: str) -> int:
    return next(b["id"] for b in common.branches() if b["code"] == code)


def _supplier(prefix: str) -> int:
    return t.sql_one("SELECT id FROM party_party WHERE name LIKE %s LIMIT 1", (prefix + "%",))["id"]


def _approve_all(req: dict, *usernames: str, comment: str = "Checked - OK (TEST)") -> dict:
    for u in usernames:
        req = approvals.act(principal(u), req["id"], decision="approve", comment=comment)
    return req


def refund_scenario() -> None:
    cashier = principal("cash.cen1")
    sale = t.sql_one("""SELECT s.id FROM motion.pos_sale s JOIN sale_point p ON p.id=s.register_id
                        WHERE p.name='CEN-T1' AND s.kind='sale' AND s.sale_date = %s
                          AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(s.payments) x WHERE x->>'method' <> 'cash')
                          AND EXISTS (SELECT 1 FROM motion.pos_sale_line l WHERE l.pos_sale_id=s.id AND l.quantity >= 1)
                        ORDER BY s.total DESC LIMIT 1""",
                     (common.today() - dt.timedelta(days=1),))
    line = t.sql_one("""SELECT id, quantity FROM motion.pos_sale_line WHERE pos_sale_id=%s AND quantity >= 1
                        ORDER BY unit_price DESC LIMIT 1""", (sale["id"],))
    req = pos.request_refund(cashier, sale_id=sale["id"], lines=[{"line_id": line["id"], "quantity": 1}],
                             reason="Customer returned item: packaging damaged (TEST)")
    _approve_all(req, "bm.joseph", comment="Inspected item; refund approved (TEST)")
    reg = next(r for r in common.registers() if r["name"] == "CEN-T1")
    pos.execute_refund(cashier, approval_id=req["id"], register_id=reg["id"], idempotency_key=f"seed-refund-{uuid.uuid4()}")


def procurement_scenarios() -> None:
    inv = principal("inv.ruth")
    ops = principal("ops.david")
    acc = principal("acc.amina")
    today = common.today()
    # (a) small requisition: full chain to payment
    a = purchasing.submit_requisition(inv, branch_id=_bid("NTH"), supplier_id=_supplier("Lakeside"),
                                      lines=[{"product_id": _pid("Soda 500ml"), "quantity": 60}],
                                      justification="Weekend demand for soft drinks (TEST)")
    _approve_all(a, "ops.david", "acc.amina")
    po = purchasing.issue_po(ops, a["id"])
    po = purchasing.receive_goods(inv, po["id"], lines=[{"line_id": ln["id"], "received_qty": ln["quantity"]} for ln in po["lines"]],
                                  note="Delivered complete (TEST)")
    inv_id = po["invoices"][0]["id"]
    purchasing.post_invoice(acc, inv_id, supplier_reference="LBD-INV-4471", invoice_date=today,
                            supplier_total=purchasing.invoice_detail(acc, inv_id)["total"])
    detail = purchasing.invoice_detail(acc, inv_id)
    pay = purchasing.request_payment(acc, inv_id, amount=detail["amount_to_pay"], reason="Due on delivery (TEST)")
    _approve_all(pay, "gm.grace")
    purchasing.record_payment(acc, pay["id"])
    # (b) mid requisition (GM level) with partial delivery of perishables-adjacent bulk food
    b = purchasing.submit_requisition(inv, branch_id=_bid("CEN"), supplier_id=_supplier("Kampala Wholesale"),
                                      lines=[{"product_id": _pid("Rice - Super"), "quantity": 100},
                                             {"product_id": _pid("Sugar - brown"), "quantity": 80}],
                                      justification="Monthly bulk foodstuff restock; Central sells ~12kg/day (TEST)",
                                      urgency="high")
    _approve_all(b, "ops.david", "acc.amina", "gm.grace")
    po = purchasing.issue_po(ops, b["id"])
    lines = []
    for ln in po["lines"]:
        q = D(ln["quantity"]) - (5 if "Rice" in (ln["description"] or "") else 0)
        lines.append({"line_id": ln["id"], "received_qty": q, "lot_ref": f"KWF-{ln['id']}"})
    po = purchasing.receive_goods(inv, po["id"], lines=lines, note="Supplier short-delivered 5kg rice; balance promised (TEST)")
    if po["invoices"]:
        bill_id = po["invoices"][0]["id"]
        purchasing.post_invoice(acc, bill_id, supplier_reference="KWF-88123", invoice_date=today,
                                supplier_total=purchasing.invoice_detail(acc, bill_id)["total"])
    # (c) high-value requisition awaiting CFO
    c = purchasing.submit_requisition(inv, branch_id=_bid("CEN"), supplier_id=_supplier("Kampala Wholesale"),
                                      lines=[{"product_id": _pid("Cooking oil 3L"), "quantity": 120},
                                             {"product_id": _pid("Wheat flour"), "quantity": 60}],
                                      justification="Festive season stock-up ahead of price increase (TEST)", urgency="high")
    _approve_all(c, "ops.david", "acc.amina", "gm.grace")
    # (d) rejected with reason
    d = purchasing.submit_requisition(inv, branch_id=_bid("STH"), supplier_id=_supplier("Kampala Wholesale"),
                                      lines=[{"product_id": _pid("Honey"), "quantity": 40}],
                                      justification="Low honey stock at South (TEST)")
    approvals.act(ops, d["id"], decision="reject",
                  comment="Honey is slow-moving; Central has surplus - request a transfer instead (TEST)")
    # (e) returned for correction
    e = purchasing.submit_requisition(inv, branch_id=_bid("NTH"), supplier_id=_supplier("Pearl"),
                                      lines=[{"product_id": _pid("Washing powder"), "quantity": 200}],
                                      justification="Restock detergents (TEST)")
    approvals.act(ops, e["id"], decision="return", comment="Quantity looks high vs 4/day sales; revise to ~60 (TEST)")


def transfer_scenarios() -> None:
    sarah, joseph = principal("bm.sarah"), principal("bm.joseph")
    tr = stock.request_transfer(sarah, from_branch_id=_bid("CEN"), to_branch_id=_bid("NTH"),
                                lines=[{"product_id": _pid("Drinking water 500ml"), "quantity": 100}],
                                reason="North running low on water ahead of weekend (TEST)")
    req = t.sql_one("SELECT * FROM motion.approval_request WHERE id=%s", (tr["approval_id"],))
    _approve_all(req, "ops.david", comment="Central has surplus (TEST)")
    stock.dispatch(joseph, tr["id"])
    stock.receive(sarah, tr["id"], lines=[{"product_id": _pid("Drinking water 500ml"), "received_qty": 95, "damaged_qty": 0}],
                  note="Counted 95 on arrival; driver says 100 loaded (TEST)")
    # a second, clean transfer
    tr2 = stock.request_transfer(principal("bm.moses"), from_branch_id=_bid("CEN"), to_branch_id=_bid("STH"),
                                 lines=[{"product_id": _pid("Honey"), "quantity": 6}], reason="Rebalance honey (TEST)")
    req2 = t.sql_one("SELECT * FROM motion.approval_request WHERE id=%s", (tr2["approval_id"],))
    _approve_all(req2, "ops.david")
    stock.dispatch(joseph, tr2["id"])
    stock.receive(principal("bm.moses"), tr2["id"], lines=[{"product_id": _pid("Honey"), "received_qty": 6}])
    # shelf write-off of expired bread at South
    moses = principal("bm.moses")
    wo = stock.request_writeoff(moses, branch_id=_bid("STH"), product_id=_pid("Bread"), quantity=D(3),
                                reason="Past best-before date, removed from shelf (TEST)", kind="expiry")
    _approve_all(wo, "ops.david")
    stock.execute_writeoff(moses, wo["id"])


def expense_scenarios() -> None:
    managers = {"CEN": "bm.joseph", "NTH": "bm.sarah", "STH": "bm.moses"}
    # Sized to the simulated sales volume (~15-17% of branch sales), so the TEST chain is modestly profitable.
    amounts = {"CEN": (350000, 550000, 90000), "NTH": (250000, 400000, 70000), "STH": (150000, 280000, 60000)}
    acc = principal("acc.amina")
    today = common.today()
    months = [(today.replace(day=1) - dt.timedelta(days=1)).replace(day=1), today.replace(day=1)]
    for month in months:
        for code, bm in managers.items():
            rent, salaries, power = amounts[code]
            for cat, amount, payee in (("rent", rent, "Prime Properties Ltd (TEST)"),
                                       ("salaries", salaries, f"{code} staff payroll (TEST)"),
                                       ("utilities", power, "Power & Water Utility (TEST)")):
                req = purchasing.submit_expense(principal(bm), branch_id=_bid(code), category=cat, payee=payee,
                                                amount=D(amount), description=f"{cat.title()} {month:%B %Y} - {code} (TEST)",
                                                document_ref=f"{cat[:3].upper()}-{code}-{month:%m%y}", incurred_on=month + dt.timedelta(days=4))
                steps = ["acc.amina", "gm.grace"] + (["hm.owner"] if amount > 1000000 else [])
                _approve_all(req, *steps)
                inv = purchasing.post_expense(acc, req["id"])
                if month != months[-1]:  # last month's bills are paid; this month's stay payable
                    pay = purchasing.request_payment(acc, inv["id"], amount=D(amount), reason="Monthly settlement (TEST)")
                    _approve_all(pay, "gm.grace", *(["hm.owner"] if amount > 1000000 else []))
                    purchasing.record_payment(acc, pay["id"], paid_on=month + dt.timedelta(days=9))


def banking_scenarios() -> None:
    """Weekly banking of the takings dropped into each branch safe."""
    from .services import finance
    acc = principal("acc.amina")
    for b in common.branches():
        weeks = t.sql("""SELECT date_trunc('week', m.date)::date AS wk, sum(l.debit - l.credit) AS net
                         FROM account_move_line l JOIN account_move m ON m.id=l.move JOIN account_account a ON a.id=l.account
                         WHERE m.state='posted' AND a.code='1.1.1.02' AND m.branch=%s AND m.date < date_trunc('week', now())::date
                         GROUP BY 1 ORDER BY 1""", (b["id"],))
        for w in weeks:
            amount = (D(w["net"]) * D("0.9")).quantize(D("1000"))
            if amount > 0:
                finance.bank_deposit(acc, branch_id=b["id"], amount=amount, date=w["wk"] + dt.timedelta(days=6),
                                     reference=f"DEP-{b['code']}-{w['wk']:%y%m%d} (TEST)",
                                     idempotency_key=f"seed-dep-{b['code']}-{w['wk']:%y%m%d}")


def misc_scenarios() -> None:
    sarah = principal("bm.sarah")
    short = t.sql_one("SELECT id FROM motion.shift WHERE status='closed' AND variance < 0 ORDER BY id LIMIT 1")
    if short:
        pos.reconcile_shift(sarah, short["id"], "Cashier recounted; shortfall accepted and noted on file (TEST)")
    catalog.change_price(principal("inv.ruth"), _pid("Sugar - brown"), new_price=D(4600),
                         reason="Supplier price increase from 1st (TEST)")


def scenarios() -> None:
    for name, fn in (("scenario_refund", refund_scenario), ("scenario_procurement", procurement_scenarios),
                     ("scenario_transfers", transfer_scenarios), ("scenario_banking", banking_scenarios),
                     ("scenario_expenses", expense_scenarios),
                     ("scenario_misc", misc_scenarios)):
        if t.run(lambda n=name: _phase_done(n), readonly=True):
            continue
        logger.info("seed %s ...", name)

        def work(fn=fn, name=name):
            with t.set_context(**common.tryton_context()):
                fn()
            _mark(name)
        t.run(work, user=0, op_key=f"seed.{name}")
