"""POS: stock + ledger effects, idempotency, oversell & concurrency, discounts, refunds, shifts."""
from __future__ import annotations

import threading
from decimal import ROUND_HALF_UP, Decimal

from conftest import Api, account_balance, ids as _ids, new_key, on_hand, pid, q, q1  # noqa: F401

D = Decimal


def r0(v) -> Decimal:
    return Decimal(v).quantize(D("1"), rounding=ROUND_HALF_UP)


def _cost(product_id: int) -> Decimal:
    return D(q1("SELECT cost_price FROM product_cost_price WHERE product=%s", (product_id,))["cost_price"])


def _sell(api: Api, register: int, lines, cash: str, key: str | None = None, **extra):
    return api.post("/api/pos/sales", {"register_id": register, "idempotency_key": key or new_key(), "lines": lines,
                                       "payments": [{"method": "cash", "amount": cash}], **extra})


def test_vat_sale_posts_exact_ledger_and_stock(open_shift, ids):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    soda = pid(ids, "Soda 500ml")
    cen = ids["branch"]["CEN"]
    before = {c: account_balance(c, cen) for c in ("1.1.1.01", "4.1.1.01", "2.2.3.01", "5.1.1.01", "1.3.1.01")}
    stock0 = on_hand(ids["storage"]["CEN"], soda)
    cost = _cost(soda)
    r = _sell(api, reg, [{"product_id": soda, "quantity": 3}], "10000")  # price read below; 10,000 covers it
    assert r.status_code == 201, r.text
    sale = r.json()
    # Independent computation from the live shelf price (incl. 18% illustrative VAT)
    price = D(q1("""SELECT gp.gross_price FROM product_gross_price gp JOIN product_product pp ON pp.template=gp.template
                    WHERE pp.id=%s AND gp.product IS NULL""", (soda,))["gross_price"])
    gross = 3 * price
    untaxed = r0(3 * (price / D("1.18")))
    vat = gross - untaxed
    assert D(sale["total"]) == gross and D(sale["total_tax"]) == vat
    after = {c: account_balance(c, cen) for c in before}
    assert after["1.1.1.01"] - before["1.1.1.01"] == gross            # till cash debited
    assert before["4.1.1.01"] - after["4.1.1.01"] == untaxed          # revenue credited ex-VAT
    assert before["2.2.3.01"] - after["2.2.3.01"] == vat              # VAT output liability
    assert after["5.1.1.01"] - before["5.1.1.01"] == r0(3 * cost)     # COGS at average cost
    assert before["1.3.1.01"] - after["1.3.1.01"] == r0(3 * cost)     # inventory relieved
    assert on_hand(ids["storage"]["CEN"], soda) == stock0 - 3
    payment = next(p for p in sale["payments"] if p["method"] == "cash")
    assert D(payment["change"]) == D(10000) - gross
    assert sale["journal_entry"]
    ev = q1("SELECT * FROM motion.audit_event WHERE action='pos.sale' AND target_id=%s", (sale["number"],))
    assert ev and ev["actor_username"] == "cash.cen1"


def test_idempotent_retry_does_not_double_post(open_shift, ids):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    water = pid(ids, "Drinking water 500ml")
    key = new_key()
    n0 = q1("SELECT count(*) AS n FROM motion.pos_sale")["n"]
    first = _sell(api, reg, [{"product_id": water, "quantity": 1}], "1000", key=key).json()
    again = _sell(api, reg, [{"product_id": water, "quantity": 1}], "1000", key=key).json()
    assert again["number"] == first["number"] and again["replayed"] is True
    assert q1("SELECT count(*) AS n FROM motion.pos_sale")["n"] == n0 + 1


def test_weighed_item_from_scale_label(open_shift, ids):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    rice = pid(ids, "Rice - Super")
    plu = q1("SELECT plu FROM motion.product_policy WHERE product_id=%s", (rice,))["plu"]
    base = f"20{plu}01250"
    check = (10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base)) % 10) % 10
    look = api.get("/api/catalog/lookup", code=base + str(check)).json()
    assert look["id"] == rice and D(look["scale"]["quantity"]) == D("1.250")
    r = _sell(api, reg, [{"product_id": rice, "quantity": look["scale"]["quantity"]}], "6000")
    assert r.status_code == 201, r.text
    assert D(r.json()["total"]) == D(6000)  # 1.25 kg x 4,800


def test_unit_items_reject_fractional_quantity(open_shift, ids):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    r = _sell(api, reg, [{"product_id": pid(ids, "Soda 500ml"), "quantity": "1.5"}], "5000")
    assert r.status_code == 422


def test_oversell_blocked(open_shift, ids):
    api, reg, _ = open_shift("cash.sth1", "STH-T1")
    honey = pid(ids, "Honey")
    have = on_hand(ids["storage"]["STH"], honey)
    r = _sell(api, reg, [{"product_id": honey, "quantity": int(have) + 1}], "9999999")
    assert r.status_code == 409 and "Not enough" in r.json()["message"]


def test_two_cashiers_race_for_last_unit(open_shift, ids, login):
    """Concurrency: exactly one of two simultaneous sales of the last unit succeeds."""
    a, reg_a, _ = open_shift("cash.nth1", "NTH-T1")
    b, reg_b, _ = open_shift("cash.nth2", "NTH-T2")
    product = pid(ids, "Cheese - cheddar")
    loc = ids["storage"]["NTH"]
    have = int(on_hand(loc, product))
    if have > 1:  # leave exactly one unit
        assert _sell(a, reg_a, [{"product_id": product, "quantity": have - 1}], str(15000 * have)).status_code == 201
    assert on_hand(loc, product) == 1
    results: list[int] = []
    barrier = threading.Barrier(2)

    def go(api, reg):
        barrier.wait()
        results.append(_sell(api, reg, [{"product_id": product, "quantity": 1}], "15000").status_code)

    threads = [threading.Thread(target=go, args=(a, reg_a)), threading.Thread(target=go, args=(b, reg_b))]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert sorted(results) == [201, 409], results
    assert on_hand(loc, product) == 0


def test_discount_limit_and_single_use_override(open_shift, ids):
    api, reg, _ = open_shift("cash.cen2", "CEN-T2")
    bread = pid(ids, "Bread")
    lines = [{"product_id": bread, "quantity": 1, "discount_pct": 20}]
    assert _sell(api, reg, lines, "5000").status_code == 403  # above cashier limit
    bad = api.post("/api/pos/overrides", {"register_id": reg, "lines": lines, "manager_username": "cash.cen1",
                                          "manager_password": "x", "reason": "damaged"})
    assert bad.status_code == 403
    from motion_api.creds import demo_password
    ov = api.post("/api/pos/overrides", {"register_id": reg, "lines": lines, "manager_username": "bm.joseph",
                                         "manager_password": demo_password("bm.joseph"), "reason": "Damaged packaging"})
    assert ov.status_code == 200, ov.text
    approval = ov.json()["approval_id"]
    r = _sell(api, reg, lines, "5000", override_approval_id=approval)
    assert r.status_code == 201 and D(r.json()["total"]) == D(4000)
    assert D(r.json()["discount_total"]) == D(1000)
    # The same override cannot be reused for another basket.
    assert _sell(api, reg, lines, "5000", override_approval_id=approval).status_code == 409  # already used


def test_refund_requires_approval_reverses_stock_and_revenue_once(open_shift, ids, login):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    oil = pid(ids, "Cooking oil 1L")
    sale = _sell(api, reg, [{"product_id": oil, "quantity": 2}], "17000").json()
    line_id = sale["lines"][0]["id"]
    stock0 = on_hand(ids["storage"]["CEN"], oil)
    rev0 = account_balance("4.1.1.01", ids["branch"]["CEN"])
    req = api.post("/api/pos/refunds/requests", {"sale_id": sale["id"], "lines": [{"line_id": line_id, "quantity": 1}],
                                                  "reason": "Leaking bottle returned"}).json()
    # Cashier cannot pay out before approval, nor approve their own request.
    assert api.post("/api/pos/refunds", {"approval_id": req["id"], "register_id": reg, "idempotency_key": new_key()}).status_code == 403
    assert login("cash.cen1").post(f"/api/approvals/{req['id']}/decision", {"decision": "approve"}).status_code == 403
    # A North branch manager has no authority over Central.
    assert login("bm.sarah").post(f"/api/approvals/{req['id']}/decision", {"decision": "approve"}).status_code == 403
    assert login("bm.joseph").post(f"/api/approvals/{req['id']}/decision", {"decision": "approve", "comment": "ok"}).status_code == 200
    r = api.post("/api/pos/refunds", {"approval_id": req["id"], "register_id": reg, "idempotency_key": new_key()})
    assert r.status_code == 201, r.text
    refund = r.json()
    assert refund["kind"] == "refund" and D(refund["total"]) == D(-8500)
    assert on_hand(ids["storage"]["CEN"], oil) == stock0 + 1
    rev1 = account_balance("4.1.1.01", ids["branch"]["CEN"])
    assert rev1 - rev0 == r0(D(8500) / D("1.00"))  # oil category has no VAT -> full 8,500 revenue reversed
    # Duplicate payout of the same approval is refused.
    assert api.post("/api/pos/refunds", {"approval_id": req["id"], "register_id": reg, "idempotency_key": new_key()}).status_code == 409
    # Cannot request more than what remains refundable.
    again = api.post("/api/pos/refunds/requests", {"sale_id": sale["id"], "lines": [{"line_id": line_id, "quantity": 2}],
                                                    "reason": "Second attempt"})
    assert again.status_code == 422


def test_shift_close_books_variance_to_over_short(login, ids):
    api = login("cash.sth1")
    cur = api.get("/api/pos/shift").json()["shift"]
    if not cur:
        cur = api.post("/api/pos/shifts", {"register_id": ids["register"]["STH-T1"], "opening_float": "50000"}).json()
    expected = D(api.get("/api/pos/shift").json()["shift"]["expected_cash_now"])
    over_short0 = account_balance("6.1.2.01", ids["branch"]["STH"])
    r = api.post(f"/api/pos/shifts/{cur['id']}/close", {"counted_cash": str(expected - 1500), "cash_drop": "0"})
    assert r.status_code == 200, r.text
    assert D(r.json()["variance"]) == D(-1500)
    assert account_balance("6.1.2.01", ids["branch"]["STH"]) - over_short0 == D(1500)  # shortage expensed
    assert q1("SELECT * FROM motion.discrepancy WHERE kind='cash_variance' AND source_id=%s", (str(cur["id"]),))
    # Cashier cannot reconcile own shift; branch manager can.
    assert api.post(f"/api/pos/shifts/{cur['id']}/reconcile", {"note": "self"}).status_code == 403
    assert login("bm.moses").post(f"/api/pos/shifts/{cur['id']}/reconcile", {"note": "Recount confirmed"}).status_code == 200


def test_mobile_money_is_recorded_as_unverified_claim(open_shift, ids):
    api, reg, _ = open_shift("cash.cen1", "CEN-T1")
    r = api.post("/api/pos/sales", {"register_id": reg, "idempotency_key": new_key(),
                                    "lines": [{"product_id": pid(ids, "Drinking water 1.5L"), "quantity": 1}],
                                    "payments": [{"method": "mobile_money", "amount": "2000", "reference": "MM123"}]})
    assert r.status_code == 201
    assert r.json()["unverified_claims"] is True
    no_ref = api.post("/api/pos/sales", {"register_id": reg, "idempotency_key": new_key(),
                                         "lines": [{"product_id": pid(ids, "Drinking water 1.5L"), "quantity": 1}],
                                         "payments": [{"method": "card", "amount": "2000"}]})
    assert no_ref.status_code == 422
