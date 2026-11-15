"""Role-specific landing dashboards. Every figure comes from a documented query."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from .. import tryton as t
from ..security import Principal
from . import approvals, common, finance, insights, pos, stock, users

ROLE_PRIORITY = ["owner", "general_manager", "accountant", "operations_manager", "inventory_manager",
                 "branch_manager", "cashier", "sysadmin"]


def primary_role(actor: Principal) -> str:
    for r in ROLE_PRIORITY:
        if r in actor.roles:
            return r
    return "cashier"


def _branch_ids(actor: Principal, branch_id: int | None) -> list[int]:
    if branch_id is not None:
        actor.require_branch(branch_id, action="dashboard.view")
        return [int(branch_id)]
    return actor.scoped_branch_ids(common.branch_ids())


def _kpis(actor: Principal, branch_ids: list[int]) -> dict:
    today = common.today()
    t_ = finance.sales_kpis(actor, d0=today, d1=today, branch_ids=branch_ids)
    y = finance.sales_kpis(actor, d0=today - dt.timedelta(days=1), d1=today - dt.timedelta(days=1), branch_ids=branch_ids)
    avg7 = t.sql_one("""SELECT COALESCE(sum(total),0)/7 AS v FROM motion.pos_sale WHERE kind='sale'
                        AND sale_date BETWEEN %s AND %s AND branch_id = ANY(%s)""",
                     (today - dt.timedelta(days=7), today - dt.timedelta(days=1), branch_ids))["v"]
    return {"today": {k: t_[k] for k in ("sales_count", "gross_sales", "refunds", "discounts", "vat", "avg_basket")},
            "yesterday": {k: y[k] for k in ("sales_count", "gross_sales", "avg_basket")},
            "avg_daily_7d": common.money(avg7), "by_method_today": t_["by_method"], "by_cashier_today": t_["by_cashier"]}


def _branch_ranking(actor: Principal) -> list[dict]:
    pnl = finance.profit_and_loss(actor, period="month")
    return sorted([{k: b[k] for k in ("code", "name", "net_sales", "gross_profit", "gross_margin_pct", "operating_profit")}
                   for b in pnl["branches"] if b["branch_id"]], key=lambda b: b["net_sales"], reverse=True)


def _approval_counts(actor: Principal) -> dict:
    if not actor.has("approvals.inbox"):
        return {}
    box = approvals.inbox(actor)
    return {"actionable": len(box["actionable"]), "overdue": sum(1 for r in box["actionable"] if r["overdue"]),
            "top": box["actionable"][:5]}


def _exceptions(actor: Principal, branch_ids: list[int]) -> dict:
    disc = t.sql("""SELECT kind, count(*) AS n, COALESCE(sum(abs(amount)),0) AS value FROM motion.discrepancy
                    WHERE status <> 'resolved' AND branch_id = ANY(%s) GROUP BY 1""", (branch_ids,))
    shifts = pos.list_shifts(actor, status="open") if actor.has("pos.history.branch") else []
    unreconciled = t.sql_one("""SELECT count(*) AS n FROM motion.shift WHERE status='closed' AND variance <> 0
                                AND branch_id = ANY(%s)""", (branch_ids,))["n"]
    return {"discrepancies": disc, "interrupted_shifts": [s for s in shifts if s["interrupted"] and s["branch_id"] in branch_ids],
            "open_shifts": len([s for s in shifts if s["branch_id"] in branch_ids]), "unreconciled_variances": unreconciled}


def _low_stock(actor: Principal, branch_ids: list[int], limit: int = 12) -> list[dict]:
    out = []
    for b in common.branches():
        if b["id"] not in branch_ids:
            continue
        rows = t.sql("""SELECT pol.product_id, pt.name, COALESCE(bp.reorder_point, pol.reorder_point) AS rp, u.symbol AS uom
                        FROM motion.product_policy pol JOIN product_product pp ON pp.id=pol.product_id
                        JOIN product_template pt ON pt.id=pp.template JOIN product_uom u ON u.id=pt.default_uom
                        LEFT JOIN motion.product_branch_policy bp ON bp.product_id=pol.product_id AND bp.branch_id=%s
                        WHERE pp.active""", (b["id"],))
        oh = common.stock_on_hand([b["storage_location_id"]], [r["product_id"] for r in rows])
        for r in rows:
            q = oh.get((b["storage_location_id"], r["product_id"]), Decimal(0))
            if r["rp"] and q <= r["rp"]:
                out.append({"branch": b["code"], "product_id": r["product_id"], "name": r["name"], "on_hand": q,
                            "reorder_point": r["rp"], "uom": r["uom"]})
    return sorted(out, key=lambda x: x["on_hand"] / x["reorder_point"] if x["reorder_point"] else 0)[:limit]


def _pipeline(actor: Principal) -> dict:
    rows = t.sql("""SELECT action_type, status, COALESCE(fulfilment_status,'') AS fulfilment, count(*) AS n, COALESCE(sum(amount),0) AS amount
                    FROM motion.approval_request WHERE action_type IN ('purchase_request','transfer')
                      AND created_at > now() - interval '60 days' AND branch_id = ANY(%s)
                    GROUP BY 1,2,3 ORDER BY 1,2""", (actor.scoped_branch_ids(common.branch_ids()),))
    cycle = t.sql_one("""SELECT avg(extract(epoch FROM (a.at - r.created_at))/3600) AS hours
                         FROM motion.approval_request r JOIN motion.approval_action a ON a.request_id=r.id AND a.to_status='approved'
                         WHERE r.action_type='purchase_request'""")["hours"]
    return {"by_status": rows, "avg_approval_hours": round(float(cycle), 1) if cycle else None}


def build(actor: Principal, *, role: str | None = None, branch_id: int | None = None) -> dict:
    role = role if role in actor.roles else primary_role(actor)
    branch_ids = _branch_ids(actor, branch_id)
    today = common.today()
    base = {"role": role, "generated_at": dt.datetime.now(common.local_tz()), "branch_ids": branch_ids,
            "test_data": True, "last_sync": _last_sync(branch_ids)}
    if role == "cashier":
        return {**base, "shift": pos.my_open_shift(actor),
                "my_sales_today": pos.list_sales(actor, branch_id=None, date_from=today, date_to=today)[:20]}
    if role == "sysadmin":
        return {**base, "devices": t.sql("SELECT id, name, branch_id, last_seen_at, revoked_at FROM motion.edge_device ORDER BY id"),
                "migrations": t.sql("SELECT version, applied_at FROM motion.schema_migration ORDER BY version"),
                "db_size": t.sql_one("SELECT pg_size_pretty(pg_database_size(current_database())) AS s")["s"]}
    data = {**base, "kpis": _kpis(actor, branch_ids)}
    fin = actor.has("finance.view")
    if fin:
        data["series"] = finance.daily_series(actor, days=30, branch_id=branch_id)
    if role == "owner":
        data.update(pnl_month=finance.profit_and_loss(actor, period="month")["consolidated"],
                    pnl_today=finance.profit_and_loss(actor, period="today")["consolidated"],
                    ranking=_branch_ranking(actor), cash=finance.cash_position(actor),
                    recovery=_recovery_summary(actor), approvals=_approval_counts(actor),
                    exceptions=_exceptions(actor, branch_ids), security=users.security_alerts(actor)[:6],
                    insights=[i for i in insights.generate(actor) if i["status"] == "new"][:6])
    elif role == "general_manager":
        data.update(pnl_month=finance.profit_and_loss(actor, period="month")["consolidated"], ranking=_branch_ranking(actor),
                    approvals=_approval_counts(actor), exceptions=_exceptions(actor, branch_ids),
                    cashiers=finance.sales_kpis(actor, d0=today - dt.timedelta(days=6), d1=today, branch_ids=branch_ids)["by_cashier"],
                    insights=[i for i in insights.generate(actor) if i["status"] == "new"][:6])
    elif role == "accountant":
        data.update(pnl_month=finance.profit_and_loss(actor, period="month")["consolidated"], cash=finance.cash_position(actor),
                    payables=finance.payables_summary(actor), approvals=_approval_counts(actor),
                    exceptions=_exceptions(actor, branch_ids),
                    drafts=t.sql("""SELECT i.id, i.description, pa.name AS supplier, i.total_amount_cache AS total
                                    FROM account_invoice i JOIN party_party pa ON pa.id=i.party
                                    WHERE i.type='in' AND i.state='draft' ORDER BY i.id DESC LIMIT 10"""),
                    unverified=t.sql_one("""SELECT count(*) AS n, COALESCE(sum(total),0) AS v FROM motion.pos_sale
                                            WHERE unverified_claims AND sale_date >= %s""", (today - dt.timedelta(days=7),)))
    elif role == "operations_manager":
        data.update(pipeline=_pipeline(actor), approvals=_approval_counts(actor),
                    transfers=[x for x in stock.list_transfers(actor) if x["status"] not in {"closed", "received", "rejected"}][:10],
                    low_stock=_low_stock(actor, branch_ids),
                    insights=[i for i in insights.generate(actor) if i["kind"] in {"transfer_suggestion", "reorder"}][:8])
    elif role == "inventory_manager":
        data.update(low_stock=_low_stock(actor, branch_ids, 20), stock=stock.overview(actor),
                    transfers=[x for x in stock.list_transfers(actor) if x["status"] in {"approved", "dispatched"}][:10],
                    approvals=_approval_counts(actor),
                    insights=[i for i in insights.generate(actor) if i["kind"] in {"near_expiry", "reorder", "slow_moving", "dead_stock"}][:10])
    elif role == "branch_manager":
        data.update(exceptions=_exceptions(actor, branch_ids), approvals=_approval_counts(actor),
                    low_stock=_low_stock(actor, branch_ids), shifts=pos.list_shifts(actor, limit=20),
                    pnl_month=finance.profit_and_loss(actor, period="month", branch_id=branch_ids[0] if len(branch_ids) == 1 else None)["consolidated"] if fin else None)
    return data


def _recovery_summary(actor: Principal) -> dict:
    r = finance.capital_recovery(actor)
    return {k: r.get(k) for k in ("status", "investment_total", "recovered", "unrecovered", "recovered_pct",
                                   "estimated_recovery_date", "weeks_of_data", "message", "test_data")}


def _last_sync(branch_ids: list[int]) -> list[dict]:
    return t.sql("""SELECT d.branch_id, d.name, d.last_seen_at FROM motion.edge_device d
                    WHERE d.revoked_at IS NULL AND d.branch_id = ANY(%s)""", (branch_ids,))
