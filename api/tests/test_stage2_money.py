"""Codex audit stage 2: idempotency (H2), one line calculator and exact refunds (H3),
shift-close serialization and blind counts (H10)."""
from __future__ import annotations

import threading
from decimal import ROUND_HALF_EVEN, Decimal as D

from conftest import account_balance, new_key, pid, q, q1
from motion_api import tryton as t
from motion_api.services import pos


def sell(api, reg, lines, payments, key=None):
    return api.post("/api/pos/sales", {"register_id": reg, "idempotency_key": key or new_key(), "lines": lines,
                                       "payments": payments})


def cash(amount):
    return [{"method": "cash", "amount": str(amount)}]


def refund(api, login, approver, sale_id, lines, reg, key=None):
    req = api.post("/api/pos/refunds/requests", {"sale_id": sale_id, "lines": lines, "reason": "Customer returned goods"})
    assert req.status_code == 201, req.text
    rid = req.json()["id"]
    assert login(approver).post(f"/api/approvals/{rid}/decision", {"decision": "approve", "comment": "ok"}).status_code == 200
    return api.post("/api/pos/refunds", {"approval_id": rid, "register_id": reg, "idempotency_key": key or f"refund-approval-{rid}"}), rid


def weighted_product():
    return q1("""SELECT p.product_id AS id, gp.gross_price AS price FROM motion.product_policy p
                 JOIN product_product pp ON pp.id=p.product_id JOIN product_gross_price gp ON gp.template=pp.template
                 AND gp.product IS NULL WHERE p.sold_by_weight ORDER BY gp.gross_price LIMIT 1""")


# ---------------------------------------------------------------- H2 idempotency

def test_sale_retry_with_same_key_replays_one_result(open_shift, ids):
    api, reg, _ = open_shift("cash.cen2", "CEN-T2")
    water = pid(ids, "Drinking water 1.5L")
    key = new_key()
    first = sell(api, reg, [{"product_id": water, "quantity": 1}], cash(2000), key)
    assert first.status_code == 201, first.text
    again = sell(api, reg, [{"product_id": water, "quantity": 1}], cash(2000), key)
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"] and again.json()["replayed"] is True
    assert q1("SELECT count(*) AS n FROM motion.pos_sale WHERE idempotency_key LIKE %s", (f"%:{key}",))["n"] == 1
    # Same key, different basket: refused, nothing posted.
    other = sell(api, reg, [{"product_id": water, "quantity": 2}], cash(4000), key)
    assert other.status_code == 409 and "different transaction" in other.json()["message"]
    assert q1("SELECT count(*) AS n FROM motion.pos_sale WHERE idempotency_key LIKE %s", (f"%:{key}",))["n"] == 1


def test_keys_are_scoped_per_cashier(open_shift, ids):
    a, reg_a, _ = open_shift("cash.nth1", "NTH-T1")
    b, reg_b, _ = open_shift("cash.nth2", "NTH-T2")
    water = pid(ids, "Drinking water 1.5L")
    key = new_key()
    ra = sell(a, reg_a, [{"product_id": water, "quantity": 1}], cash(2000), key)
    rb = sell(b, reg_b, [{"product_id": water, "quantity": 1}], cash(2000), key)
    assert ra.status_code == rb.status_code == 201 and ra.json()["id"] != rb.json()["id"]


def test_simultaneous_duplicate_submit_posts_once(open_shift, ids):
    api, reg, _ = open_shift("cash.cen2", "CEN-T2")
    water = pid(ids, "Drinking water 1.5L")
    key, out = new_key(), []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        out.append(sell(api, reg, [{"product_id": water, "quantity": 1}], cash(2000), key))

    threads = [threading.Thread(target=go) for _ in range(2)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert [r.status_code for r in out] == [201, 201], [r.text for r in out]
    assert out[0].json()["id"] == out[1].json()["id"]
    assert q1("SELECT count(*) AS n FROM motion.pos_sale WHERE idempotency_key LIKE %s", (f"%:{key}",))["n"] == 1
    assert q1("SELECT count(*) AS n FROM motion.operation WHERE key=%s", (key,))["n"] == 1


def test_refund_retry_replays_and_approval_moves_once(open_shift, ids, login):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    oil = pid(ids, "Cooking oil 1L")
    sale = sell(api, reg, [{"product_id": oil, "quantity": 2}], cash(17000)).json()
    first, rid = refund(api, login, "bm.joseph", sale["id"], [{"line_id": sale["lines"][0]["id"], "quantity": 1}], reg)
    assert first.status_code == 201, first.text
    again = api.post("/api/pos/refunds", {"approval_id": rid, "register_id": reg, "idempotency_key": f"refund-approval-{rid}"})
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert q1("SELECT refunded_qty FROM motion.pos_sale_line WHERE id=%s", (sale["lines"][0]["id"],))["refunded_qty"] == 1
    assert q1("SELECT count(*) AS n FROM motion.approval_action WHERE request_id=%s AND action='execute'", (rid,))["n"] == 1
    # A different key for the same approval cannot pay out twice.
    assert api.post("/api/pos/refunds", {"approval_id": rid, "register_id": reg, "idempotency_key": new_key()}).status_code == 409


def test_bank_deposit_retry_posts_once_and_slip_is_unique(login, ids):
    sth = ids["branch"]["STH"]
    acc = login("acc.amina")
    safe0, bank0 = account_balance("1.1.1.02", sth), account_balance("1.1.1.03", sth)
    if safe0 < 2000:
        return
    body = {"branch_id": sth, "amount": "1000", "date": "2026-11-15", "reference": "SLIP-IDEM-1", "idempotency_key": new_key()}
    r1, r2 = acc.post("/api/finance/bank-deposits", body), acc.post("/api/finance/bank-deposits", body)
    assert r1.status_code == r2.status_code == 201 and r1.json()["move"] == r2.json()["move"]
    assert account_balance("1.1.1.03", sth) - bank0 == 1000 and safe0 - account_balance("1.1.1.02", sth) == 1000
    dup = acc.post("/api/finance/bank-deposits", {**body, "idempotency_key": new_key()})
    assert dup.status_code == 409 and "already recorded" in dup.json()["message"]


# ---------------------------------------------------------------- H3 calculator and refunds

def test_weighted_line_uses_the_same_rounding_as_tryton(open_shift, ids):
    api, reg, _ = open_shift("cash.sth1", "STH-T1")
    w = weighted_product()
    price = D(w["price"])
    # Find a weight whose exact amount ends in .5 shilling (the case where half-up and half-even differ).
    grams = next((g for g in range(1, 2000) if (D(g) * price / 1000) % 1 == D("0.5")), None)
    if grams is None:
        return
    quantity = D(grams) / 1000
    expected = (quantity * price).quantize(D(1), rounding=ROUND_HALF_EVEN)
    r = sell(api, reg, [{"product_id": w["id"], "quantity": str(quantity)}], cash(expected))
    assert r.status_code == 201, r.text
    body = r.json()
    assert D(body["total"]) == expected and D(body["lines"][0]["line_gross"]) == expected
    assert D(body["lines"][0]["amount"]) == expected


def test_partial_refunds_close_the_line_exactly(open_shift, ids, login):
    api, reg, _ = open_shift("cash.sth1", "STH-T1")
    w = weighted_product()
    quantity = D("1.337")
    gross = (quantity * D(w["price"])).quantize(D(1), rounding=ROUND_HALF_EVEN)
    sale = sell(api, reg, [{"product_id": w["id"], "quantity": str(quantity)}], cash(gross)).json()
    line = sale["lines"][0]
    paid_back = D(0)
    for part in ("0.333", "0.5", "0.504"):  # three partial refunds that use the whole quantity
        r, _ = refund(api, login, "bm.moses", sale["id"], [{"line_id": line["id"], "quantity": part}], reg)
        assert r.status_code == 201, r.text
        paid_back += -D(r.json()["total"])
    row = q1("SELECT * FROM motion.pos_sale_line WHERE id=%s", (line["id"],))
    assert row["refunded_qty"] == quantity
    assert row["refunded_gross"] == row["line_gross"] == paid_back == gross
    assert row["refunded_tax"] == row["line_tax"]
    refunds = q("SELECT total, total_tax FROM motion.pos_sale WHERE refund_of=%s", (sale["id"],))
    assert -sum(r["total_tax"] for r in refunds) == D(sale["total_tax"])  # VAT fully reversed, no residue


def test_one_open_refund_per_receipt_and_no_duplicate_lines(open_shift, ids):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    oil = pid(ids, "Cooking oil 1L")
    sale = sell(api, reg, [{"product_id": oil, "quantity": 3}], cash(25500)).json()
    lid = sale["lines"][0]["id"]
    dup = api.post("/api/pos/refunds/requests", {"sale_id": sale["id"], "reason": "Returned two bottles",
                                                 "lines": [{"line_id": lid, "quantity": 1}, {"line_id": lid, "quantity": 1}]})
    assert dup.status_code == 422 and "only once" in dup.json()["message"]
    ok = api.post("/api/pos/refunds/requests", {"sale_id": sale["id"], "reason": "Returned one bottle",
                                                "lines": [{"line_id": lid, "quantity": 1}]})
    assert ok.status_code == 201
    second = api.post("/api/pos/refunds/requests", {"sale_id": sale["id"], "reason": "Another bottle",
                                                    "lines": [{"line_id": lid, "quantity": 1}]})
    assert second.status_code == 409 and "still open" in second.json()["message"]


def test_till_refunds_cash_only_up_to_the_cash_paid(open_shift, ids, login):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    oil = pid(ids, "Cooking oil 1L")
    sale = sell(api, reg, [{"product_id": oil, "quantity": 2}],
                [{"method": "mobile_money", "amount": "8500", "reference": "MM-SPLIT"}, {"method": "cash", "amount": "8500"}]).json()
    lid = sale["lines"][0]["id"]
    # The whole receipt would need 8,500 of unverified mobile money paid out as cash: refused.
    whole = api.post("/api/pos/refunds/requests", {"sale_id": sale["id"], "reason": "Both bottles leaking",
                                                   "lines": [{"line_id": lid, "quantity": 2}]})
    assert whole.status_code == 422 and "refunded by Finance" in whole.json()["message"]
    r, _ = refund(api, login, "bm.joseph", sale["id"], [{"line_id": lid, "quantity": 1}], reg)
    assert r.status_code == 201, r.text
    assert [(p["method"], D(p["amount"])) for p in r.json()["payments"]] == [("cash", D(-8500))]
    # The cash portion is now used up; the second bottle must go through Finance.
    rest = api.post("/api/pos/refunds/requests", {"sale_id": sale["id"], "reason": "Second bottle leaking",
                                                  "lines": [{"line_id": lid, "quantity": 1}]})
    assert rest.status_code == 422


def test_refund_reverses_the_original_vat_not_todays_rules(open_shift, ids, login):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    vat_item = q1("""SELECT l.product_id FROM motion.pos_sale_line l WHERE l.line_tax > 0 AND l.quantity > 0
                     ORDER BY l.id DESC LIMIT 1""")
    if not vat_item:
        return
    price = q1("""SELECT gp.gross_price FROM product_product pp JOIN product_gross_price gp ON gp.template=pp.template
                  AND gp.product IS NULL WHERE pp.id=%s""", (vat_item["product_id"],))["gross_price"]
    sale = sell(api, reg, [{"product_id": vat_item["product_id"], "quantity": 1}], cash(price)).json()
    line = sale["lines"][0]
    # Pretend the item was VAT-exempt when sold (tax rules changed since): the refund must reverse no VAT.
    t.run(lambda: t.execute("UPDATE motion.pos_sale_line SET tax_ids='{}', line_tax=0 WHERE id=%s", (line["id"],)))
    r, _ = refund(api, login, "bm.joseph", sale["id"], [{"line_id": line["id"], "quantity": 1}], reg)
    assert r.status_code == 201, r.text
    assert D(r.json()["total_tax"]) == 0 and D(r.json()["total"]) == -D(price)


# ---------------------------------------------------------------- H10 shift close vs checkout

def test_close_and_checkout_serialize(open_shift, ids, login):
    api, reg, shift = open_shift("cash.sth1", "STH-T1", "30000")
    water = pid(ids, "Drinking water 1.5L")
    expected = D(t.run(lambda: pos._with_live_totals(pos._shift(shift["id"]), reveal=True), readonly=True)["expected_cash_now"])
    barrier, out = threading.Barrier(2), {}

    def do_sale():
        barrier.wait()
        out["sale"] = sell(api, reg, [{"product_id": water, "quantity": 1}], cash(2000))

    def do_close():
        barrier.wait()
        out["close"] = login("bm.moses").post(f"/api/pos/shifts/{shift['id']}/close",
                                               {"counted_cash": str(expected), "cash_drop": "0", "note": "End of day close"})

    threads = [threading.Thread(target=do_sale), threading.Thread(target=do_close)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert out["close"].status_code == 200, out["close"].text
    closed = q1("SELECT * FROM motion.shift WHERE id=%s", (shift["id"],))
    cash_in = sum((D(p["amount"]) for s in q("SELECT payments FROM motion.pos_sale WHERE shift_id=%s", (shift["id"],))
                   for p in s["payments"] if p["method"] == "cash"), D(0))
    # Whatever the interleaving, the close counted exactly the sales recorded on the shift.
    assert closed["expected_cash"] == closed["opening_float"] + cash_in
    if out["sale"].status_code == 201:
        assert out["sale"].json()["shift_id"] == shift["id"]
    else:
        assert out["sale"].status_code == 409  # shift already closed: the sale was refused, not misfiled
