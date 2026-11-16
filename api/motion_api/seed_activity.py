"""TEST DATA business activity generated through the real services.

* 60 days of cashier shifts and sales on every register (one transaction per
  day so the seed is resumable), with small discounts, mobile-money/card and
  split tenders, a cash shortage and an interrupted shift.
* Operational scenarios (refund, procurement at each approval level, a
  rejected and a CFO-pending requisition, receiving, an inter-branch transfer
  with a 100 -> 95 discrepancy, expenses and a supplier payment) — see
  ``scenarios()``.

Shift timestamps in motion.shift are back-dated to the simulated day (TEST
DATA only); Tryton documents carry their real business dates.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import uuid
from decimal import Decimal

from . import tryton as t
from .seed import HISTORY_DAYS, PRODUCTS, _mark, _phase_done, principal
from .services import catalog, common, pos

logger = logging.getLogger("motion.seed")
D = Decimal
REGISTER_CASHIER = {"CEN-T1": "cash.cen1", "CEN-T2": "cash.cen2", "NTH-T1": "cash.nth1",
                    "NTH-T2": "cash.nth2", "STH-T1": "cash.sth1"}
FLOAT = D(200000)


def _basket(rng: random.Random, items: list[dict], weights: list[int]) -> list[dict]:
    n = rng.choices([1, 2, 3, 4, 5], weights=[25, 30, 25, 12, 8])[0]
    chosen = {}
    for it in rng.choices(items, weights=weights, k=n):
        if it["sold_by_weight"]:
            q = D(rng.choice(["0.5", "0.75", "1", "1", "1.5", "2", "2.5", "3"]))
        else:
            q = D(rng.choices([1, 2, 3, 4, 6], weights=[60, 22, 10, 5, 3])[0])
        chosen[it["id"]] = {"product_id": it["id"], "quantity": q,
                            "discount_pct": D(3) if rng.random() < 0.04 else D(0)}
    return list(chosen.values())


def _payments(rng: random.Random, total: Decimal) -> list[dict]:
    r = rng.random()
    if r < 0.70:
        tendered = ((total + 999) // 1000) * 1000
        if rng.random() < 0.3:
            tendered = ((total + 4999) // 5000) * 5000
        return [{"method": "cash", "amount": tendered}]
    if r < 0.85:
        return [{"method": "mobile_money", "amount": total, "reference": f"MM{rng.randint(10**8, 10**9)}"}]
    if r < 0.94:
        return [{"method": "card", "amount": total, "reference": f"CARD{rng.randint(10**5, 10**6)}"}]
    part = (total * D("0.4")).quantize(D("1"))
    return [{"method": "mobile_money", "amount": part, "reference": f"MM{rng.randint(10**8, 10**9)}"},
            {"method": "cash", "amount": ((total - part + 999) // 1000) * 1000}]


def _sell(cashier, register, basket, rng, day) -> dict | None:
    location = register["storage_location_id"]
    needed: dict[int, Decimal] = {}
    for line in basket:
        product_id = line["product_id"]
        needed[product_id] = needed.get(product_id, D(0)) + common.qty(line["quantity"])
    common.lock_stock(location, list(needed))
    on_hand = common.stock_on_hand([location], list(needed))
    if any(q > on_hand.get((location, pid), D(0)) for pid, q in needed.items()):
        return None
    items = {i["id"]: i for i in catalog._rows(ids=[b["product_id"] for b in basket])}
    total = sum((common.line_amount(common.qty(b["quantity"]), common.money(items[b["product_id"]]["shelf_price"]
                                                                            * (100 - b["discount_pct"]) / 100))
                 for b in basket), D(0))
    return pos.checkout(cashier, register_id=register["id"], idempotency_key=f"seed-{uuid.uuid4()}",
                        lines=basket, payments=_payments(rng, total), _sale_date=day)


def _day(day: dt.date, offset: int, items, weights, rng: random.Random, *, keep_open: bool, only: int | None = None) -> None:
    tz = common.local_tz()
    for reg in common.registers():
        if only is not None and reg["id"] != only:
            continue
        cashier = principal(REGISTER_CASHIER[reg["name"]])
        if pos.my_open_shift(cashier):
            continue
        shift = pos.open_shift(cashier, register_id=reg["id"], opening_float=FLOAT, on_date=day)
        branch = common.branch(reg["branch_id"])
        scale = {"CEN": 6, "NTH": 5, "STH": 4}[branch["code"]]
        for _ in range(rng.randint(scale - 2, scale + 1)):
            _sell(cashier, reg, _basket(rng, items, weights), rng, day)
        opened = dt.datetime.combine(day, dt.time(8, rng.randint(0, 20)), tz)
        if keep_open:
            if reg["name"] == "CEN-T2":  # interrupted: left open since yesterday evening
                opened = dt.datetime.now(tz) - dt.timedelta(hours=20)
            t.execute("UPDATE motion.shift SET opened_at=%s WHERE id=%s", (opened, shift["id"]))
            continue
        session = t.model("sale.point.cash.session")(shift["tryton_session_id"])
        expected = common.money(session.start_amount + session.balance)
        counted = expected
        note = None
        if offset == 3 and reg["name"] == "NTH-T1":
            counted, note = expected - D(2000), "Short by 2,000 at count (TEST scenario)"
        if offset == 9 and reg["name"] == "CEN-T1":
            counted, note = expected + D(1000), "Over by 1,000 (TEST scenario)"
        drop = max(counted - FLOAT, D(0))
        pos.close_shift(cashier, shift["id"], counted_cash=counted, cash_drop=drop, note=note, on_date=day)
        closed = dt.datetime.combine(day, dt.time(20, rng.randint(0, 40)), tz)
        t.execute("UPDATE motion.shift SET opened_at=%s, closed_at=%s WHERE id=%s", (opened, closed, shift["id"]))


def pos_history() -> None:
    rng = random.Random(4242)
    items_all, today = t.run(lambda: (catalog._rows(), common.today()), readonly=True)
    velocity = {p[0]: p[8] for p in PRODUCTS}
    items = [i for i in items_all if velocity.get(i["name"], 1) > 0]
    weights = [velocity.get(i["name"], 1) for i in items]
    registers = t.run(common.registers, readonly=True)
    for offset in range(HISTORY_DAYS, -1, -1):
        day = today - dt.timedelta(days=offset)
        key = f"pos_day_{day.isoformat()}"
        day_seed = rng.random()
        if t.run(lambda k=key: _phase_done(k), readonly=True):
            continue
        # One short transaction per register-day keeps locks brief while real users work.
        for reg in registers:
            rkey = f"{key}_{reg['id']}"
            if t.run(lambda k=rkey: _phase_done(k), readonly=True):
                continue

            def work(day=day, rkey=rkey, offset=offset, reg=reg, day_seed=day_seed):
                with t.set_context(**common.tryton_context()):
                    _day(day, offset, items, weights, random.Random(day_seed + reg["id"]), keep_open=(offset == 0),
                         only=reg["id"])
                _mark(rkey)
            t.run(work, user=0, op_key="seed.pos_day", retries=20)
        t.run(lambda k=key: _mark(k), user=0)
        if offset % 10 == 0:
            logger.info("seeded POS day %s", day)


def run() -> None:
    pos_history()
    try:
        from .seed_scenarios import scenarios
    except ImportError:  # scenarios module added once operations services exist
        return
    scenarios()
