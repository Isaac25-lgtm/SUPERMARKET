"""Financial reporting from the posted Tryton ledger.

Definitions (see docs/accounting-policy.md):
  Net sales        = credits - debits on revenue accounts (4.*), i.e. after refunds & discounts, excl. VAT
  COGS             = 5.1.1.01 (perpetual/continental, weighted-average cost at time of sale)
  Gross profit     = Net sales - COGS
  Shrinkage        = 5.1.1.03 (write-offs, damage, transit losses)
  Operating exp.   = 5.2.2.* (rent, salaries, utilities, ...) recognised when the bill is posted (accrual)
  Other operating  = 6.1.2.01 cash over/short
  Operating profit = Gross profit - shrinkage - operating expenses - other operating
  Est. net profit  = Operating profit (no income tax/finance cost model yet -> labelled "before tax")
  Purchase clearing (5.1.1.02) = goods received not yet invoiced / price differences. A TIMING item:
                     shown separately and EXCLUDED from profit.
  Cash flow        = movement on cash & cash-equivalent accounts (1.1.1.*), excluding transfers between them.

Branch P&L groups posted lines by account_move.branch (stamped by the
motion_branch Tryton module).  Entries without a branch are "Head office /
unallocated".  Sum of branches + unallocated == consolidated, by construction.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from .. import tryton as t
from ..errors import Invalid
from ..security import Principal
from . import common

ZERO = Decimal(0)
LINE_ORDER = ["net_sales", "cogs", "gross_profit", "shrinkage", "operating_expenses", "other_operating",
              "operating_profit", "estimated_net_profit", "purchase_clearing_timing"]


def classify(code: str) -> str | None:
    if code.startswith("4."):
        return "net_sales"
    if code == "5.1.1.01":
        return "cogs"
    if code == "5.1.1.02":
        return "purchase_clearing_timing"
    if code == "5.1.1.03":
        return "shrinkage"
    if code.startswith("5.2.") or code.startswith("5.1.4"):
        return "operating_expenses"
    if code.startswith("6.1.2") or code.startswith("6."):
        return "other_operating"
    if code.startswith("5."):
        return "operating_expenses"
    return None


def period_bounds(period: str, date_from: dt.date | None = None, date_to: dt.date | None = None) -> tuple[dt.date, dt.date, str]:
    today = common.today()
    if period == "today":
        return today, today, "Today"
    if period == "yesterday":
        y = today - dt.timedelta(days=1)
        return y, y, "Yesterday"
    if period == "week":
        start = today - dt.timedelta(days=today.weekday())
        return start, today, "This week"
    if period == "month":
        return today.replace(day=1), today, "Month to date"
    if period == "last30":
        return today - dt.timedelta(days=29), today, "Last 30 days"
    if period == "fytd":
        return dt.date(today.year, 1, 1), today, "Financial year to date"
    if period == "custom":
        if not date_from or not date_to or date_from > date_to:
            raise Invalid("Choose a valid date range.")
        if (date_to - date_from).days > 731:
            raise Invalid("Ranges are limited to 2 years.")
        return date_from, date_to, f"{date_from.isoformat()} to {date_to.isoformat()}"
    raise Invalid("Unknown period.")


def _scope(actor: Principal, branch_id: int | None) -> tuple[list[int], bool]:
    """Return (branch ids, include_unallocated)."""
    if branch_id is not None:
        actor.require_branch(branch_id, action="finance.view")
        return [int(branch_id)], False
    return actor.scoped_branch_ids(common.branch_ids()), actor.all_branches


def _ledger_rows(d0: dt.date, d1: dt.date, branch_ids: list[int], unallocated: bool) -> list[dict]:
    return t.sql("""
        SELECT a.code, a.name, m.branch, sum(l.debit) AS debit, sum(l.credit) AS credit
        FROM account_move_line l JOIN account_move m ON m.id = l.move JOIN account_account a ON a.id = l.account
        WHERE m.state = 'posted' AND m.date BETWEEN %(d0)s AND %(d1)s
          AND (a.code LIKE '4.%%' OR a.code LIKE '5.%%' OR a.code LIKE '6.%%')
          AND (m.branch = ANY(%(branches)s) OR (m.branch IS NULL AND %(unalloc)s))
        GROUP BY a.code, a.name, m.branch""", {"d0": d0, "d1": d1, "branches": branch_ids, "unalloc": unallocated})


def _empty() -> dict[str, Decimal]:
    return {k: ZERO for k in LINE_ORDER}


def _finish(p: dict[str, Decimal]) -> dict[str, Decimal]:
    p["gross_profit"] = p["net_sales"] - p["cogs"]
    p["operating_profit"] = p["gross_profit"] - p["shrinkage"] - p["operating_expenses"] - p["other_operating"]
    p["estimated_net_profit"] = p["operating_profit"]
    p["gross_margin_pct"] = (p["gross_profit"] / p["net_sales"] * 100).quantize(Decimal("0.1")) if p["net_sales"] else None
    return p


def profit_and_loss(actor: Principal, *, period: str = "month", branch_id: int | None = None,
                    date_from: dt.date | None = None, date_to: dt.date | None = None) -> dict:
    actor.require("finance.view", action="finance.pnl")
    d0, d1, label = period_bounds(period, date_from, date_to)
    branch_ids, unalloc = _scope(actor, branch_id)
    rows = _ledger_rows(d0, d1, branch_ids, unalloc)
    names = {b["id"]: b for b in common.branches()}
    by_branch: dict = defaultdict(_empty)
    consolidated = _empty()
    accounts: dict = defaultdict(lambda: {"amount": ZERO})
    for r in rows:
        cls = classify(r["code"])
        if not cls:
            continue
        amount = (r["credit"] - r["debit"]) if cls == "net_sales" else (r["debit"] - r["credit"])
        key = r["branch"] or 0
        by_branch[key][cls] += amount
        consolidated[cls] += amount
        acc = accounts[(r["code"], r["name"], cls)]
        acc["amount"] += amount
    branches = []
    for key, p in sorted(by_branch.items(), key=lambda kv: kv[0]):
        _finish(p)
        info = names.get(key)
        branches.append({"branch_id": key or None, "code": info["code"] if info else "HQ",
                         "name": info["name"] if info else "Head office / unallocated", **p})
    _finish(consolidated)
    check = sum((b["operating_profit"] for b in branches), ZERO)
    return {
        "period": {"key": period, "label": label, "from": d0, "to": d1},
        "consolidated": consolidated, "branches": branches,
        "accounts": [{"code": c, "name": n, "line": cls, "amount": v["amount"]}
                     for (c, n, cls), v in sorted(accounts.items())],
        "reconciles": check == consolidated["operating_profit"],
        "definitions": __doc__,
        "test_data": True,
    }


def daily_series(actor: Principal, *, days: int = 30, branch_id: int | None = None) -> list[dict]:
    actor.require_any("finance.view", "dashboard.branch")
    branch_ids, unalloc = _scope(actor, branch_id)
    d1 = common.today()
    d0 = d1 - dt.timedelta(days=days - 1)
    rows = t.sql("""
        SELECT m.date AS day, m.branch,
               sum(CASE WHEN a.code LIKE '4.%%' THEN l.credit - l.debit ELSE 0 END) AS net_sales,
               sum(CASE WHEN a.code = '5.1.1.01' THEN l.debit - l.credit ELSE 0 END) AS cogs
        FROM account_move_line l JOIN account_move m ON m.id=l.move JOIN account_account a ON a.id=l.account
        WHERE m.state='posted' AND m.date BETWEEN %(d0)s AND %(d1)s
          AND (m.branch = ANY(%(b)s) OR (m.branch IS NULL AND %(u)s))
        GROUP BY 1, 2 ORDER BY 1""", {"d0": d0, "d1": d1, "b": branch_ids, "u": unalloc})
    codes = {b["id"]: b["code"] for b in common.branches()}
    out: dict = {}
    for i in range(days):
        day = d0 + dt.timedelta(days=i)
        out[day] = {"day": day, "net_sales": ZERO, "gross_profit": ZERO, "by_branch": {}}
    for r in rows:
        o = out[r["day"]]
        gp = r["net_sales"] - r["cogs"]
        o["net_sales"] += r["net_sales"]
        o["gross_profit"] += gp
        code = codes.get(r["branch"], "HQ")
        o["by_branch"][code] = o["by_branch"].get(code, ZERO) + r["net_sales"]
    return list(out.values())


def sales_kpis(actor: Principal, *, d0: dt.date, d1: dt.date, branch_ids: list[int]) -> dict:
    """Operational sales metrics from POS records (gross, VAT-inclusive)."""
    s = t.sql_one("""SELECT count(*) FILTER (WHERE kind='sale') AS sales_count,
                            COALESCE(sum(total) FILTER (WHERE kind='sale'),0) AS gross_sales,
                            COALESCE(-sum(total) FILTER (WHERE kind='refund'),0) AS refunds,
                            COALESCE(sum(discount_total) FILTER (WHERE kind='sale'),0) AS discounts,
                            COALESCE(sum(total_tax),0) AS vat
                     FROM motion.pos_sale WHERE sale_date BETWEEN %s AND %s AND branch_id = ANY(%s)""",
                  (d0, d1, branch_ids))
    s["avg_basket"] = common.money(s["gross_sales"] / s["sales_count"]) if s["sales_count"] else ZERO
    s["by_method"] = t.sql("""SELECT p->>'method' AS method, sum((p->>'amount')::numeric) AS amount, count(*) AS n
                              FROM motion.pos_sale s, jsonb_array_elements(s.payments) p
                              WHERE s.kind='sale' AND s.sale_date BETWEEN %s AND %s AND s.branch_id = ANY(%s)
                              GROUP BY 1 ORDER BY 2 DESC""", (d0, d1, branch_ids))
    s["by_category"] = t.sql("""SELECT pc.name AS category, sum(l.quantity * l.unit_price) AS amount
                                FROM motion.pos_sale_line l JOIN motion.pos_sale s ON s.id=l.pos_sale_id
                                JOIN product_product pp ON pp.id=l.product_id JOIN product_template pt ON pt.id=pp.template
                                LEFT JOIN product_category pc ON pc.id=pt.account_category
                                WHERE s.sale_date BETWEEN %s AND %s AND s.branch_id = ANY(%s)
                                GROUP BY 1 ORDER BY 2 DESC""", (d0, d1, branch_ids))
    s["top_products"] = t.sql("""SELECT pt.name, sum(l.quantity) AS qty, sum(l.quantity * l.unit_price) AS amount
                                 FROM motion.pos_sale_line l JOIN motion.pos_sale s ON s.id=l.pos_sale_id
                                 JOIN product_product pp ON pp.id=l.product_id JOIN product_template pt ON pt.id=pp.template
                                 WHERE s.kind='sale' AND s.sale_date BETWEEN %s AND %s AND s.branch_id = ANY(%s)
                                 GROUP BY 1 ORDER BY 3 DESC LIMIT 10""", (d0, d1, branch_ids))
    s["by_cashier"] = t.sql("""SELECT u.full_name AS cashier, count(*) FILTER (WHERE s.kind='sale') AS sales,
                                      sum(s.total) AS amount, count(*) FILTER (WHERE s.kind='refund') AS refunds,
                                      sum(s.discount_total) AS discounts
                               FROM motion.pos_sale s JOIN motion.app_user u ON u.id=s.cashier_id
                               WHERE s.sale_date BETWEEN %s AND %s AND s.branch_id = ANY(%s)
                               GROUP BY 1 ORDER BY 3 DESC""", (d0, d1, branch_ids))
    s["by_hour"] = t.sql("""SELECT extract(hour FROM posted_at AT TIME ZONE %s)::int AS hour, count(*) AS n, sum(total) AS amount
                            FROM motion.pos_sale WHERE kind='sale' AND sale_date BETWEEN %s AND %s AND branch_id = ANY(%s)
                            GROUP BY 1 ORDER BY 1""", (str(common.local_tz()), d0, d1, branch_ids))
    return s


def cash_position(actor: Principal) -> dict:
    actor.require("finance.view", action="finance.cash")
    rows = t.sql("""SELECT a.code, a.name, sum(l.debit - l.credit) AS balance FROM account_move_line l
                    JOIN account_move m ON m.id=l.move JOIN account_account a ON a.id=l.account
                    WHERE m.state='posted' AND a.code LIKE '1.1.1.%%'
                      AND (m.branch = ANY(%s) OR (m.branch IS NULL AND %s))
                    GROUP BY 1,2 ORDER BY 1""", (actor.scoped_branch_ids(common.branch_ids()), actor.all_branches))
    total = sum((r["balance"] for r in rows), ZERO)
    return {"accounts": rows, "total": total,
            "note": "Mobile money and card balances are UNVERIFIED claims until matched to provider statements."}


def payables_summary(actor: Principal) -> dict:
    actor.require("finance.view")
    rows = t.sql("""SELECT i.state, count(*) AS n, COALESCE(sum(i.total_amount_cache),0) AS total FROM account_invoice i
                    WHERE i.type='in' AND i.state IN ('draft','validated','posted')
                      AND (i.branch = ANY(%s) OR (i.branch IS NULL AND %s)) GROUP BY 1""",
                 (actor.scoped_branch_ids(common.branch_ids()), actor.all_branches))
    open_bal = t.sql_one("""SELECT COALESCE(sum(l.credit - l.debit),0) AS open FROM account_move_line l JOIN account_account a ON a.id=l.account
                            JOIN account_move m ON m.id=l.move
                            WHERE a.code='2.1.1.01' AND l.reconciliation IS NULL AND m.state='posted'
                              AND (m.branch = ANY(%s) OR (m.branch IS NULL AND %s))""",
                         (actor.scoped_branch_ids(common.branch_ids()), actor.all_branches))
    return {"by_state": rows, "supplier_balance": open_bal["open"]}


def break_even(actor: Principal, *, period: str = "last30", branch_id: int | None = None,
               date_from: dt.date | None = None, date_to: dt.date | None = None) -> dict:
    """Operating break-even with explicit assumptions (blended contribution margin)."""
    pnl = profit_and_loss(actor, period=period, branch_id=branch_id, date_from=date_from, date_to=date_to)
    c = pnl["consolidated"]
    days = (pnl["period"]["to"] - pnl["period"]["from"]).days + 1
    sales = c["net_sales"]
    variable = c["cogs"] + c["shrinkage"]
    fixed = c["operating_expenses"] + c["other_operating"]
    assumptions = [
        "Variable costs = COGS + shrinkage; fixed costs = operating expenses posted in the period (accrual).",
        "Blended contribution margin across all products; a different sales mix changes the break-even point.",
        "Expenses only appear once bills are approved and posted; missing bills understate fixed costs.",
    ]
    if not sales:
        return {"period": pnl["period"], "status": "not_enough_data", "assumptions": assumptions,
                "message": "No posted sales in this period."}
    cm_ratio = (sales - variable) / sales
    if cm_ratio <= 0:
        return {"period": pnl["period"], "status": "negative_margin", "assumptions": assumptions,
                "message": "Contribution margin is not positive; break-even cannot be reached at current prices."}
    be_sales = common.money(fixed / cm_ratio)
    return {"period": pnl["period"], "status": "ok", "net_sales": sales, "variable_costs": variable, "fixed_costs": fixed,
            "contribution_margin_ratio": cm_ratio.quantize(Decimal("0.0001")), "break_even_sales": be_sales,
            "break_even_daily": common.money(be_sales / days), "actual_daily": common.money(sales / days),
            "margin_of_safety": common.money(sales - be_sales),
            "margin_of_safety_pct": ((sales - be_sales) / sales * 100).quantize(Decimal("0.1")),
            "assumptions": assumptions, "fixed_costs_complete": fixed > 0, "test_data": True}


def capital_recovery(actor: Principal) -> dict:
    """Investment payback from dated net operating cash flows (NOT accounting profit)."""
    actor.require("finance.view", action="finance.payback")
    inv = t.sql("SELECT * FROM motion.investment ORDER BY invested_on")
    if not inv:
        return {"status": "no_investment", "message": "Record the original investment to track capital recovery."}
    total = sum((i["amount"] for i in inv), ZERO)
    start = inv[0]["invested_on"]
    # Net cash generated: movement on cash accounts, excluding lines whose move also touches
    # equity (owner injections/withdrawals) and excluding transfers between cash accounts.
    rows = t.sql("""
        WITH cash_moves AS (
            SELECT m.id, m.date, sum(l.debit - l.credit) FILTER (WHERE a.code LIKE '1.1.1.%%') AS cash_delta,
                   bool_or(a.code LIKE '3.%%') AS touches_equity,
                   bool_and(a.code LIKE '1.1.1.%%') AS cash_only
            FROM account_move m JOIN account_move_line l ON l.move=m.id JOIN account_account a ON a.id=l.account
            WHERE m.state='posted' AND m.date >= %s GROUP BY m.id, m.date)
        SELECT date_trunc('week', date)::date AS week, sum(cash_delta) AS net_cash
        FROM cash_moves WHERE NOT touches_equity AND NOT cash_only AND cash_delta IS NOT NULL
        GROUP BY 1 ORDER BY 1""", (start,))
    cumulative = ZERO
    series = []
    for r in rows:
        cumulative += r["net_cash"]
        series.append({"week": r["week"], "net_cash": r["net_cash"], "cumulative": cumulative,
                       "unrecovered": total - cumulative})
    first = t.sql_one("SELECT min(date) AS d FROM account_move WHERE state='posted'")["d"]
    weeks = len(series)
    out = {"investment_total": total, "investments": inv, "series": series, "recovered": cumulative,
           "unrecovered": total - cumulative, "recovered_pct": (cumulative / total * 100).quantize(Decimal("0.1")),
           "data_from": first, "weeks_of_data": weeks, "test_data": any(i["is_test_data"] for i in inv),
           "assumptions": ["Recovery = cumulative net cash generated by operations since the first investment date.",
                           "Owner injections/withdrawals (equity) and transfers between cash accounts are excluded.",
                           "Projection assumes the average weekly cash generation of the last 8 weeks continues.",
                           "This is not accounting profit; stock purchased but unsold reduces recovered cash."]}
    if weeks < 8:
        out.update(status="not_enough_data",
                   message=f"Only {weeks} weeks of cash history since the first investment; at least 8 are needed for a projection.")
        return out
    recent = series[-8:]
    avg = sum((w["net_cash"] for w in recent), ZERO) / len(recent)
    if avg <= 0:
        out.update(status="not_recovering", message="Average weekly net cash generation is not positive.")
        return out
    weeks_left = (out["unrecovered"] / avg)
    out.update(status="projected", avg_weekly_net_cash=common.money(avg), weeks_to_recover=weeks_left.quantize(Decimal("0.1")),
               estimated_recovery_date=common.today() + dt.timedelta(weeks=float(weeks_left)),
               uncertainty="High — based on a short history; treat as indicative only.")
    return out


def ledger(actor: Principal, *, account_code: str | None = None, branch_id: int | None = None, days: int = 14,
           limit: int = 200) -> list[dict]:
    actor.require("finance.ledger", action="finance.ledger")
    branch_ids, unalloc = _scope(actor, branch_id)
    return t.sql("""
        SELECT m.id AS move_id, m.number, m.date, m.origin, m.description, j.code AS journal, b.code AS branch_code,
               a.code AS account_code, a.name AS account_name, l.debit, l.credit, l.description AS line_description,
               pa.name AS party
        FROM account_move_line l JOIN account_move m ON m.id=l.move JOIN account_account a ON a.id=l.account
        JOIN account_journal j ON j.id=m.journal LEFT JOIN motion_branch b ON b.id=m.branch
        LEFT JOIN party_party pa ON pa.id=l.party
        WHERE m.state='posted' AND m.date >= %(d)s AND (%(acc)s::text IS NULL OR a.code LIKE %(accp)s)
          AND (m.branch = ANY(%(b)s) OR (m.branch IS NULL AND %(u)s))
        ORDER BY m.date DESC, m.id DESC, l.id LIMIT %(lim)s""",
        {"d": common.today() - dt.timedelta(days=days), "acc": account_code, "accp": f"{account_code}%" if account_code else None,
         "b": branch_ids, "u": unalloc, "lim": limit})


def trial_balance(actor: Principal) -> dict:
    actor.require("finance.ledger")
    rows = t.sql("""SELECT a.code, a.name, sum(l.debit) AS debit, sum(l.credit) AS credit FROM account_move_line l
                    JOIN account_move m ON m.id=l.move JOIN account_account a ON a.id=l.account
                    WHERE m.state='posted' GROUP BY 1,2 ORDER BY 1""")
    d = sum((r["debit"] for r in rows), ZERO)
    c = sum((r["credit"] for r in rows), ZERO)
    return {"accounts": rows, "total_debit": d, "total_credit": c, "balanced": d == c}


def bank_deposit(actor: Principal, *, branch_id: int, amount: Decimal, date: dt.date, reference: str) -> dict:
    """Bank the takings held in the safe: Dr bank / Cr safe (branch-stamped journal entry)."""
    from ..audit import record

    actor.require("payment.record", action="finance.bank_deposit")
    actor.require_branch(branch_id, action="finance.bank_deposit")
    amount = common.money(amount)
    if amount <= 0:
        raise Invalid("Amount must be positive.")
    if not reference or len(reference.strip()) < 3:
        raise Invalid("Enter the bank slip reference.")
    safe = t.sql_one("""SELECT COALESCE(sum(l.debit - l.credit), 0) AS b FROM account_move_line l JOIN account_move m ON m.id=l.move
                        JOIN account_account a ON a.id=l.account WHERE m.state='posted' AND a.code='1.1.1.02' AND m.branch=%s""",
                     (branch_id,))["b"]
    if amount > safe:
        raise Invalid(f"Only {common.money(safe)} is recorded in this branch's safe.")
    Move = t.model("account.move")
    Period = t.model("account.period")
    cid = common.company_id()
    with t.set_context(**common.tryton_context(branch_id)), t.transaction().set_user(actor.tryton_user_id or 0):
        move, = Move.create([{
            "company": cid, "journal": t.model("ir.model.data").get_id("account", "journal_cash"),
            "period": Period.find(cid, date=date).id, "date": date, "branch": branch_id,
            "description": f"Bank deposit {reference.strip()}",
            "lines": [("create", [
                {"account": common.account_by_code("1.1.1.03").id, "debit": amount, "credit": Decimal(0)},
                {"account": common.account_by_code("1.1.1.02").id, "debit": Decimal(0), "credit": amount}])]}])
        Move.post([move])
        common.expect_state(move, "posted", what="bank deposit")
    record(actor=actor, action="finance.bank_deposit", target_type="account.move", target_id=move.number,
           branch_id=branch_id, amount=amount, reason=reference)
    return {"move": move.number, "amount": amount}
