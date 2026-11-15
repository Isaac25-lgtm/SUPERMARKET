"""TEST DATA seed (idempotent, phase-tracked in motion.app_setting 'demo_seed').

Everything created here is synthetic and labelled TEST: party names carry
"(TEST)", barcodes use the unassigned GS1 prefix 999, user names say TEST.
Business activity (sales, shifts, purchases, transfers, expenses) is created
through the platform's own services, so it exercises the real code paths and
the same Tryton postings as live use.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
from decimal import Decimal

from . import tryton as t
from .creds import DEMO_USERS, demo_password
from .security import hash_password, load_principal
from .services import catalog, common
from .settings import Settings

logger = logging.getLogger("motion.seed")
D = Decimal
HISTORY_DAYS = int(__import__('os').environ.get('MOTION_SEED_HISTORY_DAYS', '56'))

SUPPLIERS = [
    "Kampala Wholesale Foods (TEST)", "Lakeside Beverages Distributors (TEST)", "Pearl Household Supplies (TEST)",
    "Fresh Farms Cooperative (TEST)", "Nile Dairy & Bakery Co. (TEST)", "Equator Personal Care (TEST)",
]

# name, category, shelf price UGX, cost ratio, sold_by_weight, perishable, shelf_life_days, supplier idx, velocity
PRODUCTS = [
    ("Rice - Super grade (per kg)", "Foodstuff - dry & bulk", 4800, .80, True, False, None, 0, 9),
    ("Sugar - brown (per kg)", "Foodstuff - dry & bulk", 4500, .84, True, False, None, 0, 10),
    ("Maize flour / posho (per kg)", "Foodstuff - dry & bulk", 3200, .78, True, False, None, 0, 12),
    ("Beans - nambale (per kg)", "Foodstuff - dry & bulk", 5500, .76, True, False, None, 0, 7),
    ("Groundnuts - red (per kg)", "Foodstuff - dry & bulk", 9000, .74, True, False, None, 0, 3),
    ("Millet flour (per kg)", "Foodstuff - dry & bulk", 6000, .75, True, False, None, 0, 3),
    ("Salt - iodised 1kg", "Groceries - packaged", 1500, .70, False, False, None, 0, 6),
    ("Wheat flour 2kg", "Groceries - packaged", 9000, .82, False, False, None, 0, 4),
    ("Spaghetti 500g", "Groceries - packaged", 4000, .75, False, False, None, 0, 4),
    ("Tomato paste 70g", "Groceries - packaged", 1500, .70, False, False, None, 0, 5),
    ("Baked beans 420g", "Groceries - packaged", 4500, .74, False, False, None, 0, 2),
    ("Corn flakes 500g", "Groceries - packaged", 14000, .78, False, False, None, 0, 1),
    ("Peanut butter 400g", "Groceries - packaged", 8000, .72, False, False, None, 0, 2),
    ("Honey 500g", "Groceries - packaged", 12000, .70, False, False, None, 0, 0),  # slow mover
    ("Biscuits - glucose 100g", "Groceries - packaged", 1000, .70, False, False, None, 0, 8),
    ("Instant noodles 70g", "Groceries - packaged", 1000, .72, False, False, None, 0, 9),
    ("Tea leaves 250g", "Groceries - packaged", 3500, .72, False, False, None, 0, 5),
    ("Instant coffee 100g", "Groceries - packaged", 9500, .74, False, False, None, 0, 1),
    ("Cooking oil 1L", "Cooking oil & spreads", 8500, .84, False, False, None, 0, 8),
    ("Cooking oil 3L", "Cooking oil & spreads", 24000, .85, False, False, None, 0, 3),
    ("Margarine 500g", "Cooking oil & spreads", 7000, .78, False, False, None, 0, 3),
    ("Tomatoes (per kg)", "Fresh produce", 3500, .65, True, True, 6, 3, 8),
    ("Onions - red (per kg)", "Fresh produce", 4000, .66, True, True, 14, 3, 7),
    ("Irish potatoes (per kg)", "Fresh produce", 2500, .64, True, True, 21, 3, 8),
    ("Cabbage (each)", "Fresh produce", 2000, .60, False, True, 10, 3, 4),
    ("Bananas - sweet (per kg)", "Fresh produce", 3000, .62, True, True, 5, 3, 5),
    ("Matooke bunch (each)", "Fresh produce", 25000, .72, False, True, 7, 3, 2),
    ("Beef - steak cut (per kg)", "Meat & fish", 20000, .78, True, True, 3, 3, 4),
    ("Tilapia - fresh (per kg)", "Meat & fish", 16000, .76, True, True, 2, 3, 3),
    ("Chicken - whole (each)", "Meat & fish", 24000, .80, False, True, 4, 3, 2),
    ("Fresh milk 500ml", "Dairy & bakery", 1800, .78, False, True, 5, 4, 12),
    ("UHT milk 1L", "Dairy & bakery", 4500, .80, False, True, 90, 4, 4),
    ("Yoghurt 500ml", "Dairy & bakery", 4000, .74, False, True, 14, 4, 4),
    ("Bread - white 600g", "Dairy & bakery", 5000, .80, False, True, 4, 4, 10),
    ("Eggs - tray of 30", "Dairy & bakery", 13000, .82, False, True, 21, 4, 5),
    ("Butter 250g", "Dairy & bakery", 9000, .76, False, True, 60, 4, 1),
    ("Cheese - cheddar 250g", "Dairy & bakery", 15000, .74, False, True, 45, 4, 1),
    ("Soda 500ml", "Beverages", 2000, .70, False, False, None, 1, 14),
    ("Drinking water 500ml", "Beverages", 1000, .60, False, False, None, 1, 15),
    ("Drinking water 1.5L", "Beverages", 2000, .62, False, False, None, 1, 8),
    ("Fruit juice 1L", "Beverages", 6000, .72, False, False, None, 1, 4),
    ("Energy drink 250ml", "Beverages", 3500, .70, False, False, None, 1, 4),
    ("Bar soap - laundry 800g", "Household", 4500, .78, False, False, None, 2, 6),
    ("Washing powder 1kg", "Household", 9000, .78, False, False, None, 2, 4),
    ("Toilet paper 4-pack", "Household", 5000, .74, False, False, None, 2, 5),
    ("Dishwashing liquid 750ml", "Household", 4500, .72, False, False, None, 2, 3),
    ("Bleach 750ml", "Household", 3500, .70, False, False, None, 2, 2),
    ("Matches - pack of 10", "Household", 1500, .66, False, False, None, 2, 4),
    ("Candles - pack of 6", "Household", 3000, .70, False, False, None, 2, 2),
    ("Insect spray 300ml", "Household", 12000, .74, False, False, None, 2, 0),  # dead stock
    ("Charcoal - small bag", "Household", 10000, .72, False, False, None, 2, 3),
    ("Toothpaste 100ml", "Personal care", 5000, .74, False, False, None, 5, 4),
    ("Bathing soap 175g", "Personal care", 3000, .72, False, False, None, 5, 5),
    ("Petroleum jelly 250ml", "Personal care", 4500, .70, False, False, None, 5, 3),
    ("Body lotion 400ml", "Personal care", 13000, .72, False, False, None, 5, 1),
    ("Sanitary pads - pack", "Personal care", 4500, .74, False, False, None, 5, 3),
    ("Shampoo 400ml", "Personal care", 9000, .72, False, False, None, 5, 1),
    ("Disposable razors 5-pack", "Personal care", 3000, .66, False, False, None, 5, 1),
    ("Baby diapers - pack of 30", "Personal care", 32000, .82, False, False, None, 5, 1),
    ("Mosquito coil - box", "Household", 2500, .68, False, False, None, 2, 2),
]


def _ean(n: int) -> str:
    base = f"999{n:09d}"
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base))
    return base + str((10 - total % 10) % 10)


def _phase_done(name: str) -> bool:
    row = t.sql_one("SELECT value FROM motion.app_setting WHERE key='demo_seed'")
    return bool(row and row["value"].get(name))


def _mark(name: str) -> None:
    t.execute("""INSERT INTO motion.app_setting (key, value) VALUES ('demo_seed', jsonb_build_object(%s::text, true))
                 ON CONFLICT (key) DO UPDATE SET value = motion.app_setting.value || jsonb_build_object(%s::text, true)""",
              (name, name))


def _phase(name: str, fn, **ctx):
    done = t.run(lambda: _phase_done(name), readonly=True)
    if done:
        logger.info("seed phase %s already done", name)
        return
    logger.info("seed phase %s ...", name)

    def work():
        with t.set_context(**common.tryton_context(**ctx)):
            fn()
        _mark(name)
    t.run(work, user=0)


def principal(username: str):
    row = t.sql_one("SELECT id FROM motion.app_user WHERE username=%s", (username,))
    return load_principal(row["id"])


def seed_users() -> None:
    from .services.users import _tryton_identity

    codes = {b["code"]: b["id"] for b in common.branches()}
    owner_id = None
    for username, full_name, roles, branch_codes in DEMO_USERS:
        if t.sql_one("SELECT 1 AS x FROM motion.app_user WHERE username=%s", (username,)):
            continue
        tu, emp = _tryton_identity(full_name, username)
        all_b = branch_codes == "*"
        row = t.sql_one("""INSERT INTO motion.app_user (username, full_name, password_hash, all_branches,
                           must_change_password, tryton_user_id, tryton_employee_id, created_by)
                           VALUES (%s,%s,%s,%s,false,%s,%s,%s) RETURNING id""",
                        (username, full_name, hash_password(demo_password(username)), all_b, tu, emp, owner_id))
        owner_id = owner_id or row["id"]
        for r in roles:
            t.execute("INSERT INTO motion.user_role (user_id, role_code, granted_by) VALUES (%s,%s,%s)", (row["id"], r, owner_id))
        if not all_b:
            for code in branch_codes:
                t.execute("INSERT INTO motion.user_branch (user_id, branch_id, granted_by) VALUES (%s,%s,%s)",
                          (row["id"], codes[code], owner_id))
    t.execute("""INSERT INTO motion.audit_event (actor_username, action, outcome, target_type, reason)
                 VALUES ('system', 'seed.users', 'success', 'user', 'TEST accounts created by seed')""")


def seed_catalog() -> None:
    supplier_ids = [common.ensure_party(name).id for name in SUPPLIERS]
    cats = {r["name"]: r["id"] for r in t.sql("SELECT id, name FROM product_category")}
    inv = principal("inv.ruth")
    for i, (name, cat, price, ratio, weight, perish, life, sup, velocity) in enumerate(PRODUCTS, start=1):
        if t.sql_one("SELECT 1 AS x FROM product_template WHERE name=%s", (name,)):
            continue
        plu = f"{10000 + i}" if weight else None
        cost = (D(price) * D(str(ratio))).quantize(D("1"))
        reorder = D(15 if weight else 12) if velocity else D(2)
        catalog.create_product(inv, name=name, category_id=cats[cat], shelf_price=D(price), cost_price=cost,
                               sku=f"SKU-{i:04d}", barcode=None if weight else _ean(i), sold_by_weight=weight,
                               plu=plu, perishable=perish, shelf_life_days=life, reorder_point=reorder,
                               reorder_qty=reorder * 4, preferred_supplier=supplier_ids[sup], audit=False)


def seed_opening_stock() -> None:
    Move = t.model("stock.move")
    rng = random.Random(20261115)
    start = common.today() - dt.timedelta(days=HISTORY_DAYS + 1)
    equity = common.account_by_code("3.4.4.01")
    lost = common.lost_found_location_id()
    items = catalog._rows()
    velocity = {p[0]: p[8] for p in PRODUCTS}
    life = {p[0]: p[6] for p in PRODUCTS}
    uoms = {r["id"]: r["default_uom"] for r in t.sql("SELECT pp.id, pt.default_uom FROM product_product pp JOIN product_template pt ON pt.id=pp.template")}
    company = common.company_id()
    for b in common.branches():
        moves = []
        for it in items:
            v = velocity.get(it["name"], 3)
            mult = {"CEN": 1.3, "NTH": 1.0, "STH": 0.7}[b["code"]]
            q = D(max(6, int(v * HISTORY_DAYS * 0.55 * mult + rng.randint(5, 25))))
            if v == 0:
                q = D(rng.randint(14, 30))
            if it["name"].startswith("Honey") and b["code"] == "STH":
                q = D(4)
            moves.append({"product": it["id"], "unit": uoms[it["id"]], "quantity": float(q), "from_location": lost,
                          "to_location": b["storage_location_id"], "effective_date": start, "company": company})
        with t.set_context(motion_lost_found_account=equity.id, motion_branch=b["id"]):
            created = Move.create(moves)
            Move.do(created)
        for it, m in zip(items, moves):
            if life.get(it["name"]):
                t.execute("""INSERT INTO motion.stock_batch (product_id, branch_id, lot_ref, expiry_date, received_qty,
                             received_at, source_type) VALUES (%s,%s,%s,%s,%s,%s,'opening')""",
                          (it["id"], b["id"], f"OPEN-{b['code']}-{it['id']}", start + dt.timedelta(days=life[it["name"]]),
                           m["quantity"], dt.datetime.combine(start, dt.time(8), common.local_tz())))


def seed_investment() -> None:
    owner = principal("hm.owner")
    t.execute("""INSERT INTO motion.investment (label, amount, invested_on, note, is_test_data, created_by)
                 VALUES ('Initial chain fit-out & stock (TEST)', 45000000, %s, 'TEST DATA - illustrative capital outlay', true, %s)""",
              (common.today() - dt.timedelta(days=HISTORY_DAYS + 2), owner.user_id))


def seed_investment_rescale() -> None:
    """Earlier seeds used 380M; rescale the TEST investment to the simulated chain's size."""
    t.execute("UPDATE motion.investment SET amount = 45000000 WHERE is_test_data AND amount = 380000000")


def run(settings: Settings, activity: bool = True) -> None:
    import os

    if not os.environ.get("MOTION_SEED_PASSWORD_SECRET"):
        raise RuntimeError("Set MOTION_SEED_PASSWORD_SECRET to seed demo users")
    t.configure(settings)
    _phase("users", seed_users)
    _phase("catalog", seed_catalog)
    _phase("opening_stock", seed_opening_stock)
    _phase("investment", seed_investment)
    _phase("investment_v2", seed_investment_rescale)
    if not activity:  # fixtures for automated tests: no generated history
        return
    from . import seed_activity

    seed_activity.run()
    logger.info("TEST DATA seed complete")
