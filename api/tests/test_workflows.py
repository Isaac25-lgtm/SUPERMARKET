"""Transfers, write-offs, procurement chain, payments, expenses and approval rules."""
from __future__ import annotations

from decimal import Decimal

from conftest import account_balance, on_hand, pid, q, q1

D = Decimal


def _approve(login, req_id, *users):
    for u in users:
        r = login(u).post(f"/api/approvals/{req_id}/decision", {"decision": "approve", "comment": "ok"})
        assert r.status_code == 200, (u, r.text)


def test_transfer_100_dispatched_95_received(login, ids):
    water = pid(ids, "Drinking water 500ml")
    cen, nth = ids["storage"]["CEN"], ids["storage"]["NTH"]
    from motion_api import tryton as t
    from motion_api.services import common
    transit = t.run(common.transit_location_id, readonly=True)
    c0, n0, t0 = on_hand(cen, water), on_hand(nth, water), on_hand(transit, water)
    revenue0 = account_balance("4.1.1.01")
    moves0 = q1("SELECT count(*) AS n FROM account_move")["n"]
    sarah, joseph = login("bm.sarah"), login("bm.joseph")
    tr = sarah.post("/api/transfers", {"from_branch_id": ids["branch"]["CEN"], "to_branch_id": ids["branch"]["NTH"],
                                       "lines": [{"product_id": water, "quantity": 100}], "reason": "Test transfer"}).json()
    assert joseph.post(f"/api/transfers/{tr['id']}/dispatch").status_code == 409  # not approved yet
    _approve(login, tr["approval_id"], "ops.david")
    assert sarah.post(f"/api/transfers/{tr['id']}/dispatch").status_code == 403  # receiving branch cannot dispatch
    assert joseph.post(f"/api/transfers/{tr['id']}/dispatch").status_code == 200
    assert on_hand(cen, water) == c0 - 100 and on_hand(transit, water) == t0 + 100
    assert sarah.post(f"/api/transfers/{tr['id']}/receive", {"lines": []}).status_code == 422
    assert on_hand(nth, water) == n0
    r = sarah.post(f"/api/transfers/{tr['id']}/receive", {"lines": [{"product_id": water, "received_qty": 95}], "note": "5 missing"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "received_with_discrepancy"
    assert on_hand(nth, water) == n0 + 95
    assert on_hand(transit, water) == t0 + 5           # 5 unresolved, still visible in transit
    assert on_hand(cen, water) + on_hand(nth, water) + on_hand(transit, water) == c0 + n0 + t0  # nothing vanished
    assert account_balance("4.1.1.01") == revenue0       # no revenue from internal transfer
    assert q1("SELECT count(*) AS n FROM account_move")["n"] == moves0  # internal moves create no journal entries
    disc = q1("SELECT * FROM motion.discrepancy WHERE source_type='transfer' AND source_id=%s", (str(tr["id"]),))
    assert disc["kind"] == "transfer_shortage" and D(disc["quantity"]) == 5
    # The receiving branch (North) sees the discrepancy in its queue: it records late arrivals.
    seen = sarah.get("/api/stock/discrepancies", branch_id=ids["branch"]["NTH"]).json()["discrepancies"]
    assert disc["id"] in {d["id"] for d in seen}
    assert next(d for d in seen if d["id"] == disc["id"])["to_branch_code"] == "NTH"
    # Authorised write-off of the 5 units: transit -> lost & found, expensed as shrinkage (not COGS)
    shrink0, cogs0 = account_balance("5.1.1.03"), account_balance("5.1.1.01")
    wo = login("inv.ruth").post("/api/stock/writeoffs", {"branch_id": ids["branch"]["CEN"], "product_id": water, "quantity": 5,
                                                         "reason": "Lost in transit, driver report filed", "discrepancy_id": disc["id"]}).json()
    _approve(login, wo["id"], "ops.david")
    assert login("inv.ruth").post(f"/api/stock/writeoffs/{wo['id']}/execute").status_code == 200
    assert on_hand(transit, water) == t0
    cost = D(q1("SELECT cost_price FROM product_cost_price WHERE product=%s", (water,))["cost_price"])
    assert account_balance("5.1.1.03") - shrink0 == (5 * cost).quantize(D("1"))
    assert account_balance("5.1.1.01") == cogs0
    assert q1("SELECT status FROM motion.transfer WHERE id=%s", (tr["id"],))["status"] == "closed"


def test_purchase_bands_route_to_right_approvers(login, ids):
    inv = login("inv.ruth")
    sup = q1("SELECT id FROM party_party WHERE name LIKE 'Kampala Wholesale%%'")["id"]
    soda = pid(ids, "Soda 500ml")

    def req(qty):
        r = inv.post("/api/procurement/requisitions", {"branch_id": ids["branch"]["CEN"], "supplier_id": sup, "justification": "Band test",
                                                       "lines": [{"product_id": soda, "quantity": qty, "unit_cost": "1000"}]})
        assert r.status_code == 201, r.text
        return r.json()
    small, mid, big = req(100), req(500), req(1500)          # 100k, 500k, 1.5M
    assert [s["role"] for s in small["steps"]] == ["operations_manager", "accountant"]
    assert [s["role"] for s in mid["steps"]] == ["operations_manager", "accountant", "general_manager"]
    assert [s["role"] for s in big["steps"]] == ["operations_manager", "accountant", "general_manager", "owner"]
    # Order is enforced: GM cannot approve before operations and finance.
    assert login("gm.grace").post(f"/api/approvals/{big['id']}/decision", {"decision": "approve"}).status_code == 403
    _approve(login, big["id"], "ops.david", "acc.amina", "gm.grace")
    # A branch manager forging a call cannot do the CFO step.
    assert login("bm.joseph").post(f"/api/approvals/{big['id']}/decision", {"decision": "approve"}).status_code == 403
    assert q1("SELECT status FROM motion.approval_request WHERE id=%s", (big["id"],))["status"] == "pending"
    _approve(login, big["id"], "hm.owner")
    assert q1("SELECT status FROM motion.approval_request WHERE id=%s", (big["id"],))["status"] == "approved"


def test_same_person_cannot_approve_two_steps(login, ids):
    owner = login("hm.owner")
    # Give ops.david the accountant role too, then try to approve both steps.
    uid = ids["user"]["ops.david"]
    owner.put(f"/api/admin/users/{uid}/access", {"roles": ["operations_manager", "accountant"], "branch_ids": [], "all_branches": True,
                                                "reason": "dual role test"})
    sup = q1("SELECT id FROM party_party WHERE name LIKE 'Lakeside%%'")["id"]
    r = login("inv.ruth").post("/api/procurement/requisitions", {"branch_id": ids["branch"]["NTH"], "supplier_id": sup,
                               "justification": "Dual role test", "lines": [{"product_id": pid(ids, "Soda 500ml"), "quantity": 10}]}).json()
    david = login("ops.david", fresh=True)
    assert david.post(f"/api/approvals/{r['id']}/decision", {"decision": "approve"}).status_code == 200
    second = david.post(f"/api/approvals/{r['id']}/decision", {"decision": "approve"})
    assert second.status_code == 403 and "different person" in second.json()["message"]
    owner.put(f"/api/admin/users/{uid}/access", {"roles": ["operations_manager"], "branch_ids": [], "all_branches": True, "reason": "restore"})


def test_full_procurement_to_payment(login, ids):
    inv, ops, acc = login("inv.ruth"), login("ops.david"), login("acc.amina")
    sup = q1("SELECT id FROM party_party WHERE name LIKE 'Kampala Wholesale%%'")["id"]
    sugar = pid(ids, "Sugar - brown")
    stock0 = on_hand(ids["storage"]["STH"], sugar)
    req = inv.post("/api/procurement/requisitions", {"branch_id": ids["branch"]["STH"], "supplier_id": sup, "justification": "Restock",
                                                     "lines": [{"product_id": sugar, "quantity": 40, "unit_cost": "3700"}]}).json()
    assert req["amount"] == "148000.00"
    assert ops.post(f"/api/procurement/requisitions/{req['id']}/issue-po").status_code == 403  # not approved
    _approve(login, req["id"], "ops.david", "acc.amina")
    po = ops.post(f"/api/procurement/requisitions/{req['id']}/issue-po").json()
    line = po["lines"][0]
    # Partial delivery: 30 of 40, with a lot and expiry
    po = inv.post(f"/api/procurement/orders/{po['id']}/receive", {"lines": [{"line_id": line["id"], "received_qty": 30, "lot_ref": "LOT-A",
                                                                             "expiry_date": "2027-06-30"}], "note": "short 10"}).json()
    assert on_hand(ids["storage"]["STH"], sugar) == stock0 + 30
    assert D(po["lines"][0]["pending"]) == 10
    assert q1("SELECT fulfilment_status FROM motion.approval_request WHERE id=%s", (req["id"],))["fulfilment_status"] == "partially_received"
    invoice_id = po["invoices"][0]["id"]
    later = inv.post(f"/api/procurement/orders/{po['id']}/receive",
                     {"lines": [{"line_id": line["id"], "received_qty": 10}], "note": "Balance delivered"})
    assert later.status_code == 200, later.text
    assert on_hand(ids["storage"]["STH"], sugar) == stock0 + 40
    assert len(later.json()["invoices"]) == 2
    detail = acc.get(f"/api/finance/invoices/{invoice_id}").json()
    assert detail["three_way_match"] is True and D(detail["lines"][0]["quantity"]) == 30
    payable0 = account_balance("2.1.1.01")
    post = {"supplier_reference": "KWF-T1", "invoice_date": "2026-11-15"}
    # the total typed from the supplier's own invoice must agree with the bill (VAT included)
    wrong = acc.post(f"/api/finance/invoices/{invoice_id}/post", {**post, "supplier_total": "111001"})
    assert wrong.status_code == 409 and "supplier's invoice" in wrong.json()["message"]
    assert acc.get(f"/api/finance/invoices/{invoice_id}").json()["state"] == "draft"
    assert acc.post(f"/api/finance/invoices/{invoice_id}/post", {**post, "supplier_total": "111000"}).status_code == 200
    assert payable0 - account_balance("2.1.1.01") == D(111000)  # liability recognised: 30 x 3,700
    # Payment is a separate approval; requester cannot approve it.
    pay = acc.post(f"/api/finance/invoices/{invoice_id}/payment-request", {"amount": "111000", "reason": "Due"}).json()
    assert acc.post(f"/api/finance/payments/{pay['id']}/record").status_code == 403
    assert acc.post(f"/api/approvals/{pay['id']}/decision", {"decision": "approve"}).status_code == 403
    _approve(login, pay["id"], "gm.grace")
    bank0 = account_balance("1.1.1.03")
    assert acc.post(f"/api/finance/payments/{pay['id']}/record").status_code == 200
    assert bank0 - account_balance("1.1.1.03") == D(111000)
    assert acc.get(f"/api/finance/invoices/{invoice_id}").json()["state"] == "paid"


def test_matched_bill_posts_when_product_has_default_input_vat(login, ids):
    """Tryton warns when bill taxes differ from the product default (here: illustrative input VAT on
    packaged groceries). A bill matched to its approved PO is posted with the PO's taxes, and the
    acknowledgement is recorded in the audit trail (regression found in the final browser run)."""
    inv, ops, acc = login("inv.ruth"), login("ops.david"), login("acc.amina")
    sup = q1("SELECT id FROM party_party WHERE name LIKE 'Kampala Wholesale%%'")["id"]
    flour = pid(ids, "Wheat flour")
    req = inv.post("/api/procurement/requisitions", {"branch_id": ids["branch"]["NTH"], "supplier_id": sup, "justification": "Restock",
                                                     "lines": [{"product_id": flour, "quantity": 5, "unit_cost": "7380"}]}).json()
    _approve(login, req["id"], "ops.david", "acc.amina")
    po = ops.post(f"/api/procurement/requisitions/{req['id']}/issue-po").json()
    po = inv.post(f"/api/procurement/orders/{po['id']}/receive", {"lines": [{"line_id": po["lines"][0]["id"], "received_qty": 5}]}).json()
    bill = po["invoices"][0]
    assert D(bill["total"]) == D(36900)  # draft bills show their live total, not a missing cache
    r = acc.post(f"/api/finance/invoices/{bill['id']}/post", {"supplier_reference": "KWF-VAT-1", "invoice_date": "2026-11-15",
                                                            "supplier_total": "36900"})
    assert r.status_code == 200, r.text
    ev = q1("SELECT after_state FROM motion.audit_event WHERE action='invoice.post' ORDER BY id DESC LIMIT 1")
    assert ev["after_state"]["taxes_follow_po_not_product_default"]


def test_bill_with_vat_not_on_the_approved_order_cannot_post(login, ids):
    """A tax added to a draft bill line (not on the approved PO line) blocks posting, even when the
    supplier total typed in agrees with the inflated bill."""
    from motion_api import tryton as t

    inv, ops, acc = login("inv.ruth"), login("ops.david"), login("acc.amina")
    sup = q1("SELECT id FROM party_party WHERE name LIKE 'Kampala Wholesale%%'")["id"]
    req = inv.post("/api/procurement/requisitions", {"branch_id": ids["branch"]["NTH"], "supplier_id": sup, "justification": "Restock",
                                                     "lines": [{"product_id": pid(ids, "Sugar - brown"), "quantity": 4, "unit_cost": "3700"}]}).json()
    _approve(login, req["id"], "ops.david", "acc.amina")
    po = ops.post(f"/api/procurement/requisitions/{req['id']}/issue-po").json()
    po = inv.post(f"/api/procurement/orders/{po['id']}/receive", {"lines": [{"line_id": po["lines"][0]["id"], "received_qty": 4}]}).json()
    bill_id = po["invoices"][0]["id"]
    assert acc.get(f"/api/finance/invoices/{bill_id}").json()["three_way_match"] is True
    line = q1("SELECT id FROM account_invoice_line WHERE invoice=%s AND type='line'", (bill_id,))
    po_taxes = {r["tax"] for r in q("""SELECT pt.tax FROM "purchase_line-account_tax" pt JOIN account_invoice_line il
                                       ON il.origin = 'purchase.line,' || pt.line WHERE il.id=%s""", (line["id"],))}
    extra = q1("SELECT id FROM account_tax WHERE parent IS NULL AND NOT (id = ANY(%s)) ORDER BY id LIMIT 1", (sorted(po_taxes),))
    t.run(lambda: t.execute('INSERT INTO "account_invoice_line-account_tax" (line, tax) VALUES (%s,%s)', (line["id"], extra["id"])))
    detail = acc.get(f"/api/finance/invoices/{bill_id}").json()
    assert detail["three_way_match"] is False and detail["lines"][0]["tax_match"] is False
    r = acc.post(f"/api/finance/invoices/{bill_id}/post", {"supplier_reference": "KWF-TAX-X", "invoice_date": "2026-11-15",
                                                          "supplier_total": str(detail["total"])})
    assert r.status_code == 409 and "VAT" in r.json()["message"]
    assert acc.get(f"/api/finance/invoices/{bill_id}").json()["state"] == "draft"


def test_payables_summary_counts_cached_and_live_totals(login, ids):
    acc = login("acc.amina")
    summary = {r["state"]: r for r in acc.get("/api/dashboard").json()["payables"]["by_state"]}
    bills = acc.get("/api/finance/payables").json()["invoices"]
    for state in ("draft", "posted"):
        mine = [b for b in bills if b["state"] == state]
        if mine and len(bills) < 300:
            assert summary[state]["n"] == len(mine)
            assert D(summary[state]["total"]) == sum(D(b["total"]) for b in mine)


def test_rejection_and_return_need_reasons(login, ids):
    sup = q1("SELECT id FROM party_party WHERE name LIKE 'Pearl%%'")["id"]
    r = login("inv.ruth").post("/api/procurement/requisitions", {"branch_id": ids["branch"]["NTH"], "supplier_id": sup, "justification": "Need it",
                               "lines": [{"product_id": pid(ids, "Bleach"), "quantity": 10}]}).json()
    ops = login("ops.david")
    assert ops.post(f"/api/approvals/{r['id']}/decision", {"decision": "reject"}).status_code == 422
    assert ops.post(f"/api/approvals/{r['id']}/decision", {"decision": "return", "comment": "Use transfer"}).status_code == 200
    # C1: generic resubmission is disabled; the requester cancels and raises a corrected request.
    rs = login("inv.ruth").post(f"/api/approvals/{r['id']}/resubmit", {"payload": r["payload"], "amount": "5000", "justification": "Reduced"})
    assert rs.status_code == 409
    assert login("inv.ruth").post(f"/api/approvals/{r['id']}/cancel", {"reason": "Will resubmit corrected"}).status_code == 200


def test_expense_accrual_then_payment(login, ids):
    bm, acc = login("bm.moses"), login("acc.amina")
    opex0 = account_balance("5.2.2.03", ids["branch"]["STH"])
    e = bm.post("/api/finance/expenses", {"branch_id": ids["branch"]["STH"], "category": "utilities", "payee": "Power Test Co",
                                          "amount": "250000", "description": "Electricity test", "incurred_on": "2026-11-10"}).json()
    _approve(login, e["id"], "acc.amina", "gm.grace")
    assert bm.post(f"/api/finance/expenses/{e['id']}/post").status_code == 403  # submitter cannot post
    inv = acc.post(f"/api/finance/expenses/{e['id']}/post").json()
    assert account_balance("5.2.2.03", ids["branch"]["STH"]) - opex0 == D(250000)  # expense recognised on posting (accrual)
    assert inv["state"] == "posted" and D(inv["amount_to_pay"]) == D(250000)


def test_unconfigured_amount_is_denied(login, ids):
    owner = login("hm.owner")
    pol = next(p for p in owner.get("/api/admin/policies").json()["policies"] if p["action_type"] == "transfer")
    # Narrow the transfer policy to 0-10, so a larger transfer has no authority (default deny).
    r = owner.put(f"/api/admin/policies/{pol['id']}", {"min_amount": "0", "max_amount": "10", "steps": pol["steps"], "reason": "Deny test band"})
    assert r.status_code == 200
    t = login("bm.sarah").post("/api/transfers", {"from_branch_id": ids["branch"]["CEN"], "to_branch_id": ids["branch"]["NTH"],
                                                  "lines": [{"product_id": pid(ids, "Soda 500ml"), "quantity": 5}], "reason": "deny"})
    assert t.status_code == 403 and "default deny" in t.json()["message"]
    new_id = r.json()["id"]
    owner.put(f"/api/admin/policies/{new_id}", {"min_amount": "0", "max_amount": None, "steps": pol["steps"], "reason": "Restore"})
    ev = q("SELECT * FROM motion.audit_event WHERE action='admin.policy.change' ORDER BY id DESC LIMIT 2")
    assert len(ev) == 2 and ev[0]["before_state"]["max"] == "10.00"
