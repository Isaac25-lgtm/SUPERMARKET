"""Shared lookups used by services (all run inside a Tryton transaction)."""
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from .. import tryton as t
from ..errors import NotFound
from ..settings import get_settings

ZERO = Decimal(0)


def money(value) -> Decimal:
    """UGX has no minor unit in practice: round half-up to whole shillings."""
    return Decimal(str(value or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def line_amount(quantity, unit_gross) -> Decimal:
    """THE line calculator (Codex H3): quantity x tax-inclusive unit price, rounded half-even
    to whole shillings exactly like Tryton's currency rounding. The browser till
    (web/src/lib/money.ts) and the offline till (branch-edge) use the same rule."""
    return (Decimal(str(quantity)) * Decimal(str(unit_gross))).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)


def qty(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.001"))


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def today() -> dt.date:
    return dt.datetime.now(local_tz()).date()


def company_id() -> int:
    row = t.sql_one("SELECT id FROM company_company ORDER BY id LIMIT 1")
    if not row:
        raise RuntimeError("Company not configured; run bootstrap")
    return row["id"]


def tryton_context(branch_id: int | None = None, **extra) -> dict:
    ctx = {"company": company_id(), "language": "en", "_skip_warnings": True}
    if branch_id:
        ctx["motion_branch"] = branch_id
    ctx.update(extra)
    return ctx


def branches() -> list[dict]:
    return t.sql("""
        SELECT b.id, b.code, b.name, b.warehouse, w.storage_location AS storage_location_id,
               w.input_location AS input_location_id, w.output_location AS output_location_id
        FROM motion_branch b JOIN stock_location w ON w.id = b.warehouse
        WHERE b.active ORDER BY b.code""")


def branch(branch_id: int) -> dict:
    for b in branches():
        if b["id"] == int(branch_id):
            return b
    raise NotFound("Branch not found.")


def branch_ids() -> list[int]:
    return [b["id"] for b in branches()]


def registers(branch_id: int | None = None) -> list[dict]:
    rows = t.sql("""
        SELECT p.id, p.name, p.branch AS branch_id, p.storage_location AS storage_location_id
        FROM sale_point p WHERE (%s::int IS NULL OR p.branch = %s) ORDER BY p.name""", (branch_id, branch_id))
    return rows


def register(register_id: int) -> dict:
    row = t.sql_one("SELECT p.id, p.name, p.branch AS branch_id, p.storage_location AS storage_location_id "
                    "FROM sale_point p WHERE p.id=%s", (register_id,))
    if not row:
        raise NotFound("Register not found.")
    return row


def next_ref(prefix: str, seq: str) -> str:
    n = t.sql_one(f"SELECT nextval('motion.{seq}') AS n")["n"]
    return f"{prefix}-{n:06d}"


def stock_on_hand(location_ids: list[int], product_ids: list[int] | None = None) -> dict[tuple[int, int], Decimal]:
    """Quantity of done stock moves per (location, product), in the product's default UoM."""
    if not location_ids:
        return {}
    rows = t.sql("""
        SELECT loc, product, SUM(q) AS qty FROM (
            SELECT to_location AS loc, product, internal_quantity AS q FROM stock_move
             WHERE state='done' AND to_location = ANY(%(locs)s) AND (%(prods)s::int[] IS NULL OR product = ANY(%(prods)s))
            UNION ALL
            SELECT from_location AS loc, product, -internal_quantity AS q FROM stock_move
             WHERE state='done' AND from_location = ANY(%(locs)s) AND (%(prods)s::int[] IS NULL OR product = ANY(%(prods)s))
        ) m GROUP BY loc, product""", {"locs": location_ids, "prods": product_ids})
    return {(r["loc"], r["product"]): qty(r["qty"]) for r in rows}


def transit_location_id() -> int:
    return t.model("ir.model.data").get_id("stock", "location_transit")


def lost_found_location_id() -> int:
    return t.model("ir.model.data").get_id("stock", "location_lost_found")


def supplier_location_id() -> int:
    return t.model("ir.model.data").get_id("stock", "location_supplier")


def account_by_code(code: str):
    Account = t.model("account.account")
    found = Account.search([("code", "=", code)], limit=1)
    if not found:
        raise NotFound(f"Account {code} not configured")
    return found[0]


def user_brief(user_ids: set[int]) -> dict[int, str]:
    ids = [i for i in user_ids if i]
    if not ids:
        return {}
    return {r["id"]: r["full_name"] for r in t.sql(
        "SELECT id, full_name FROM motion.app_user WHERE id = ANY(%s)", (ids,))}


def product_names(product_ids: set[int]) -> dict[int, dict]:
    ids = [i for i in product_ids if i]
    if not ids:
        return {}
    rows = t.sql("""
        SELECT pp.id, pt.name, pp.code AS sku FROM product_product pp
        JOIN product_template pt ON pt.id = pp.template WHERE pp.id = ANY(%s)""", (ids,))
    return {r["id"]: r for r in rows}


def ensure_party(name: str):
    """Return a party (creating it with an address — Tryton invoices/purchases need one)."""
    Party = t.model("party.party")
    found = Party.search([("name", "=", name)], limit=1)
    party = found[0] if found else Party.create([{"name": name, "addresses": [("create", [{}])]}])[0]
    if not party.addresses:
        t.model("party.address").create([{"party": party.id}])
        party = Party(party.id)
    return party


def expect_state(record, *states: str, what: str) -> None:
    """Tryton workflow transitions silently skip records in an invalid source
    state; assert the outcome so a skipped step fails loudly (and rolls back)."""
    record = type(record)(record.id)
    if record.state not in states:
        raise RuntimeError(f"{what}: expected state {states}, got {record.state!r} (workflow step was skipped)")
