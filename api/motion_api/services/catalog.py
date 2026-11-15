"""Products, prices, barcodes and stock levels.

Writes go through the Tryton ORM; listings use read-only SQL over Tryton's
tables for speed.  Shelf price = Tryton ``gross_price`` (tax-inclusive, what
the POS charges at a tax-included register); ``list_price`` is derived from it
by reverse-computing the product's taxes so revenue and VAT split correctly.

Scale labels (foodstuff sold by weight): EAN-13 starting with ``2``:
  ``2 0 PPPPP WWWWW C`` -> PLU PPPPP, weight WWWWW grams   (prefix 20-24)
  ``2 5 PPPPP VVVVV C`` -> PLU PPPPP, price VVVVV x 10 UGX (prefix 25-29)
The layout is configurable per deployment in app_setting 'scale_barcode'.
"""
from __future__ import annotations

from decimal import Decimal

from .. import tryton as t
from ..audit import record
from ..errors import Conflict, Invalid, NotFound
from ..security import Principal
from . import common

CATALOG_SQL = """
SELECT pp.id, pp.code AS sku, pt.id AS template_id, pt.name, pt.type, pt.salable, pt.purchasable,
       pc.id AS category_id, pc.name AS category, u.symbol AS uom, u.digits AS uom_digits,
       gp.gross_price AS shelf_price, lp.list_price, cp.cost_price,
       (SELECT code FROM product_identifier pi WHERE pi.product = pp.id ORDER BY pi.sequence NULLS LAST, pi.id LIMIT 1) AS barcode,
       pol.sold_by_weight, pol.plu, pol.perishable, pol.shelf_life_days, pol.reorder_point, pol.reorder_qty,
       pol.preferred_supplier, pol.lead_time_days, pp.active,
       EXISTS (SELECT 1 FROM "product_category-customer-account_tax" r WHERE r.category = pc.id) AS vat
FROM product_product pp
JOIN product_template pt ON pt.id = pp.template
LEFT JOIN product_category pc ON pc.id = pt.account_category
JOIN product_uom u ON u.id = pt.default_uom
LEFT JOIN product_gross_price gp ON gp.template = pt.id AND gp.product IS NULL AND gp.company = %(company)s
LEFT JOIN product_list_price lp ON lp.template = pt.id AND lp.company = %(company)s
LEFT JOIN product_cost_price cp ON cp.product = pp.id AND cp.company = %(company)s
LEFT JOIN motion.product_policy pol ON pol.product_id = pp.id
WHERE pt.type = 'goods' AND (%(active)s::bool IS NULL OR pp.active = %(active)s)
  AND (%(ids)s::int[] IS NULL OR pp.id = ANY(%(ids)s))
  AND (%(category)s::int IS NULL OR pc.id = %(category)s)
  AND (%(q)s::text IS NULL OR pt.name ILIKE %(q)s OR pp.code ILIKE %(q)s OR pol.plu = %(qraw)s
       OR EXISTS (SELECT 1 FROM product_identifier pi WHERE pi.product = pp.id AND pi.code = %(qraw)s))
ORDER BY pt.name
"""


def _rows(*, ids=None, q=None, category=None, active=True) -> list[dict]:
    qraw = q.strip() if q else None
    rows = t.sql(CATALOG_SQL, {"company": common.company_id(), "ids": ids, "category": category,
                               "q": f"%{qraw}%" if qraw else None, "qraw": qraw, "active": active})
    for r in rows:
        r["sold_by_weight"] = bool(r["sold_by_weight"])
        r["perishable"] = bool(r["perishable"])
        r["shelf_price"] = common.money(r["shelf_price"]) if r["shelf_price"] is not None else None
        r["cost_price"] = Decimal(r["cost_price"] or 0).quantize(Decimal("0.01"))
    return rows


def categories(actor: Principal) -> list[dict]:
    actor.require("catalog.view")
    return t.sql("""SELECT pc.id, pc.name, EXISTS (SELECT 1 FROM "product_category-customer-account_tax" r
                    WHERE r.category = pc.id) AS vat FROM product_category pc ORDER BY pc.name""")


def _reorder_points(product_ids: list[int], branch_id: int) -> dict[int, tuple[Decimal, Decimal]]:
    rows = t.sql("""SELECT p.product_id, COALESCE(b.reorder_point, p.reorder_point) AS rp,
                           COALESCE(b.reorder_qty, p.reorder_qty) AS rq
                    FROM motion.product_policy p
                    LEFT JOIN motion.product_branch_policy b ON b.product_id=p.product_id AND b.branch_id=%s
                    WHERE p.product_id = ANY(%s)""", (branch_id, product_ids))
    return {r["product_id"]: (common.qty(r["rp"]), common.qty(r["rq"])) for r in rows}


def list_products(actor: Principal, *, branch_id: int | None = None, q: str | None = None,
                  category: int | None = None, low_stock: bool = False, include_inactive: bool = False) -> dict:
    actor.require("catalog.view")
    rows = _rows(q=q, category=category, active=None if include_inactive else True)
    scope = common.branches()
    if branch_id is not None:
        actor.require_branch(branch_id, action="catalog.view")
        scope = [b for b in scope if b["id"] == int(branch_id)]
    else:
        scope = [b for b in scope if actor.can_branch(b["id"])]
    pids = [r["id"] for r in rows]
    stock = common.stock_on_hand([b["storage_location_id"] for b in scope], pids) if pids else {}
    show_cost = actor.has("finance.view") or actor.has("catalog.manage")
    out = []
    for r in rows:
        per_branch = {}
        for b in scope:
            on_hand = stock.get((b["storage_location_id"], r["id"]), Decimal(0))
            per_branch[b["code"]] = on_hand
        r["stock"] = per_branch
        r["stock_total"] = sum(per_branch.values(), Decimal(0))
        if not show_cost:
            r.pop("cost_price", None)
        out.append(r)
    if scope:
        for b in scope:
            rp = _reorder_points(pids, b["id"]) if pids else {}
            for r in out:
                point = rp.get(r["id"], (Decimal(0), Decimal(0)))[0]
                if point and r["stock"][b["code"]] <= point:
                    r.setdefault("low_at", []).append(b["code"])
    if low_stock:
        out = [r for r in out if r.get("low_at")]
    return {"branches": [{"id": b["id"], "code": b["code"], "name": b["name"]} for b in scope], "items": out}


def product(actor: Principal, product_id: int) -> dict:
    actor.require("catalog.view")
    rows = _rows(ids=[product_id], active=None)
    if not rows:
        raise NotFound("Product not found.")
    r = rows[0]
    visible = [b for b in common.branches() if actor.can_branch(b["id"])]
    stock = common.stock_on_hand([b["storage_location_id"] for b in visible], [product_id])
    r["stock"] = {b["code"]: stock.get((b["storage_location_id"], product_id), Decimal(0)) for b in visible}
    r["price_history"] = t.sql("""SELECT pc.changed_at, pc.old_price, pc.new_price, pc.reason, u.full_name
                                  FROM motion.price_change pc JOIN motion.app_user u ON u.id=pc.changed_by
                                  WHERE pc.product_id=%s ORDER BY pc.changed_at DESC LIMIT 20""", (product_id,))
    r["batches"] = batches_for(product_id, [b["id"] for b in visible])
    if not (actor.has("finance.view") or actor.has("catalog.manage")):
        r.pop("cost_price", None)
    return r


def _scale_layout() -> dict:
    row = t.sql_one("SELECT value FROM motion.app_setting WHERE key='scale_barcode'")
    return row["value"] if row else {"weight_prefixes": ["20", "21", "22", "23", "24"],
                                     "price_prefixes": ["25", "26", "27", "28", "29"], "price_multiplier": 10}


def ean13_valid(code: str) -> bool:
    if len(code) != 13 or not code.isdigit():
        return False
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(code[:12]))
    return (10 - total % 10) % 10 == int(code[12])


def lookup(actor: Principal, code: str, branch_id: int | None = None) -> dict:
    """Resolve a scanned barcode, a PLU or a SKU to a product (+ weight/price for scale labels)."""
    actor.require("catalog.view")
    code = (code or "").strip()
    if not code:
        raise Invalid("Scan or type a code.")
    rows = t.sql("""SELECT pp.id FROM product_product pp LEFT JOIN motion.product_policy pol ON pol.product_id = pp.id
                    WHERE pp.active AND (pp.code = %(c)s OR pol.plu = %(c)s
                          OR EXISTS (SELECT 1 FROM product_identifier pi WHERE pi.product = pp.id AND pi.code = %(c)s))
                    LIMIT 2""", {"c": code})
    scale = None
    if not rows and ean13_valid(code) and code[0] == "2":
        layout = _scale_layout()
        plu = code[2:7]
        value = int(code[7:12])
        row = t.sql_one("SELECT product_id AS id FROM motion.product_policy WHERE plu=%s", (plu,))
        if row:
            rows = [row]
            if code[:2] in layout["weight_prefixes"]:
                scale = {"kind": "weight", "quantity": common.qty(Decimal(value) / 1000)}
            elif code[:2] in layout["price_prefixes"]:
                scale = {"kind": "price", "amount": common.money(value * layout.get("price_multiplier", 10))}
    if not rows:
        raise NotFound(f"No product matches '{code}'.")
    if len(rows) > 1:
        raise Conflict("That code matches more than one product; fix the catalog.")
    item = _rows(ids=[rows[0]["id"]])[0]
    if scale and scale["kind"] == "price" and item["shelf_price"]:
        scale["quantity"] = common.qty(Decimal(scale["amount"]) / item["shelf_price"])
    item["scale"] = scale
    if branch_id is not None:
        actor.require_branch(branch_id, action="catalog.lookup")
        b = common.branch(branch_id)
        item["on_hand"] = common.stock_on_hand([b["storage_location_id"]], [item["id"]]).get(
            (b["storage_location_id"], item["id"]), Decimal(0))
    if not (actor.has("finance.view") or actor.has("catalog.manage")):
        item.pop("cost_price", None)
    return item


def _taxes_for_category(category_id: int):
    Category = t.model("product.category")
    return list(Category(category_id).customer_taxes)


def _list_price_from_gross(gross: Decimal, category_id: int) -> Decimal:
    Tax = t.model("account.tax")
    taxes = _taxes_for_category(category_id)
    if not taxes:
        return Decimal(gross).quantize(Decimal("0.0001"))
    return Decimal(Tax.reverse_compute(Decimal(gross), taxes, common.today())).quantize(Decimal("0.0001"))


def create_product(actor: Principal, *, name: str, category_id: int, shelf_price: Decimal, cost_price: Decimal,
                   sku: str | None = None, barcode: str | None = None, sold_by_weight: bool = False,
                   plu: str | None = None, perishable: bool = False, shelf_life_days: int | None = None,
                   reorder_point: Decimal = Decimal(0), reorder_qty: Decimal = Decimal(0),
                   preferred_supplier: int | None = None, audit: bool = True) -> dict:
    actor.require("catalog.manage", action="catalog.product.create")
    if not name.strip():
        raise Invalid("Product name is required.")
    shelf_price = common.money(shelf_price)
    if shelf_price <= 0 or Decimal(cost_price) < 0:
        raise Invalid("Prices must be positive.")
    if plu and (len(plu) != 5 or not plu.isdigit()):
        raise Invalid("PLU must be 5 digits.")
    if barcode and t.sql_one("SELECT 1 AS x FROM product_identifier WHERE code=%s", (barcode,)):
        raise Conflict("That barcode is already used by another product.")
    if plu and t.sql_one("SELECT 1 AS x FROM motion.product_policy WHERE plu=%s", (plu,)):
        raise Conflict("That PLU is already used.")
    Template = t.model("product.template")
    MD = t.model("ir.model.data")
    uom = MD.get_id("product", "uom_kilogram" if sold_by_weight else "uom_unit")
    values = {
        "name": name.strip(), "type": "goods", "default_uom": uom, "sale_uom": uom, "purchase_uom": uom,
        "salable": True, "purchasable": True, "account_category": category_id,
        "cost_price_method": "average", "list_price": _list_price_from_gross(shelf_price, category_id),
        "gross_price": shelf_price,
        "products": [("create", [{"code": sku or None, "cost_price": Decimal(cost_price),
                                   "identifiers": [("create", [{"type": "ean" if barcode and len(barcode) == 13 else None,
                                                                "code": barcode}])] if barcode else []}])],
    }
    template, = Template.create([values])
    product_id = template.products[0].id
    t.execute("""INSERT INTO motion.product_policy (product_id, sold_by_weight, plu, perishable, shelf_life_days,
                 reorder_point, reorder_qty, preferred_supplier) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
              (product_id, sold_by_weight, plu, perishable, shelf_life_days, reorder_point, reorder_qty, preferred_supplier))
    if audit:
        record(actor=actor, action="catalog.product.create", target_type="product", target_id=product_id,
               after={"name": name, "shelf_price": shelf_price, "barcode": barcode, "plu": plu, "sold_by_weight": sold_by_weight})
    return {"id": product_id}


def update_product(actor: Principal, product_id: int, *, name: str | None = None, category_id: int | None = None,
                   barcode: str | None = None, reorder_point: Decimal | None = None, reorder_qty: Decimal | None = None,
                   perishable: bool | None = None, shelf_life_days: int | None = None,
                   preferred_supplier: int | None = None, active: bool | None = None) -> dict:
    actor.require("catalog.manage", action="catalog.product.update")
    Product = t.model("product.product")
    Template = t.model("product.template")
    before = _rows(ids=[product_id], active=None)
    if not before:
        raise NotFound("Product not found.")
    prod = Product(product_id)
    tvals = {}
    if name:
        tvals["name"] = name.strip()
    if category_id and category_id != before[0]["category_id"]:
        tvals["account_category"] = category_id
        tvals["list_price"] = _list_price_from_gross(before[0]["shelf_price"], category_id)
    if tvals:
        Template.write([prod.template], tvals)
    if active is not None:
        Product.write([prod], {"active": active})
    if barcode is not None:
        Identifier = t.model("product.identifier")
        if barcode and t.sql_one("SELECT 1 AS x FROM product_identifier WHERE code=%s AND product<>%s", (barcode, product_id)):
            raise Conflict("That barcode is already used by another product.")
        Identifier.delete(Identifier.search([("product", "=", product_id)]))
        if barcode:
            Identifier.create([{"product": product_id, "code": barcode, "type": "ean" if len(barcode) == 13 else None}])
    t.execute("""UPDATE motion.product_policy SET reorder_point=COALESCE(%s, reorder_point), reorder_qty=COALESCE(%s, reorder_qty),
                 perishable=COALESCE(%s, perishable), shelf_life_days=COALESCE(%s, shelf_life_days),
                 preferred_supplier=COALESCE(%s, preferred_supplier), updated_at=now() WHERE product_id=%s""",
              (reorder_point, reorder_qty, perishable, shelf_life_days, preferred_supplier, product_id))
    after = _rows(ids=[product_id], active=None)[0]
    record(actor=actor, action="catalog.product.update", target_type="product", target_id=product_id,
           before={k: before[0].get(k) for k in ("name", "category", "barcode", "reorder_point", "active")},
           after={k: after.get(k) for k in ("name", "category", "barcode", "reorder_point", "active")})
    return after


def change_price(actor: Principal, product_id: int, *, new_price: Decimal, reason: str) -> dict:
    actor.require("catalog.price", action="catalog.price.change")
    if not reason or len(reason.strip()) < 3:
        raise Invalid("Give a reason for the price change.")
    new_price = common.money(new_price)
    if new_price <= 0:
        raise Invalid("Price must be positive.")
    rows = _rows(ids=[product_id], active=None)
    if not rows:
        raise NotFound("Product not found.")
    old = rows[0]
    Template = t.model("product.template")
    Product = t.model("product.product")
    Template.write([Product(product_id).template], {
        "gross_price": new_price, "list_price": _list_price_from_gross(new_price, old["category_id"])})
    t.execute("INSERT INTO motion.price_change (product_id, old_price, new_price, reason, changed_by) VALUES (%s,%s,%s,%s,%s)",
              (product_id, old["shelf_price"], new_price, reason, actor.user_id))
    record(actor=actor, action="catalog.price.change", target_type="product", target_id=product_id, reason=reason,
           before={"name": old["name"], "shelf_price": old["shelf_price"]}, after={"shelf_price": new_price})
    return _rows(ids=[product_id])[0]


def batches_for(product_id: int, branch_ids: list[int]) -> list[dict]:
    """Batches with ESTIMATED remaining quantity (newest receipts hold current stock)."""
    out = []
    for b in common.branches():
        if b["id"] not in branch_ids:
            continue
        on_hand = common.stock_on_hand([b["storage_location_id"]], [product_id]).get(
            (b["storage_location_id"], product_id), Decimal(0))
        remaining = max(on_hand, Decimal(0))
        for batch in t.sql("""SELECT * FROM motion.stock_batch WHERE product_id=%s AND branch_id=%s
                              ORDER BY received_at DESC, id DESC""", (product_id, b["id"])):
            est = min(remaining, common.qty(batch["received_qty"]))
            remaining -= est
            out.append({**batch, "branch_code": b["code"], "estimated_remaining": est})
    return out
