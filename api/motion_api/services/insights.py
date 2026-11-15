"""Motion Intelligence — rule-based, evidence-linked insights (method: rules-v1).

AI here is assistive: insights are computed from read-only, branch-scoped
business data with transparent formulas, carry their evidence and data
window, and never post, approve, transfer, re-price or refund anything.  A
suggested action only pre-fills a DRAFT the user must submit through the
normal approval workflow.  An LLM narrative layer is optional and disabled by
default (MOTION_AI_LLM_ENABLED); nothing here depends on it.
"""
from __future__ import annotations

import datetime as dt
import statistics
from decimal import Decimal

from .. import tryton as t
from ..audit import record
from ..errors import Forbidden, Invalid
from ..security import Principal, utcnow
from . import catalog, common

METHOD = "rules-v1"
ZERO = Decimal(0)


def _sales_qty(days: int) -> dict[tuple[int, int], Decimal]:
    rows = t.sql("""SELECT s.branch_id, l.product_id, sum(l.quantity) AS q FROM motion.pos_sale_line l
                    JOIN motion.pos_sale s ON s.id=l.pos_sale_id WHERE s.sale_date > %s GROUP BY 1,2""",
                 (common.today() - dt.timedelta(days=days),))
    return {(r["branch_id"], r["product_id"]): Decimal(r["q"]) for r in rows}


def _ins(key, kind, severity, title, detail, evidence, branch=None, action=None, confidence="medium"):
    return {"key": key, "kind": kind, "severity": severity, "title": title, "detail": detail, "evidence": evidence,
            "branch": branch, "suggested_action": action, "method": METHOD, "confidence": confidence,
            "generated_at": utcnow()}


def generate(actor: Principal, branch_id: int | None = None) -> list[dict]:
    actor.require("insights.view", action="insights.view")
    branches = [b for b in common.branches() if actor.can_branch(b["id"]) and (branch_id is None or b["id"] == branch_id)]
    if branch_id is not None and not branches:
        actor.require_branch(branch_id, action="insights.view")
    items = {r["id"]: r for r in catalog._rows()}
    policy = {r["product_id"]: r for r in t.sql("SELECT * FROM motion.product_policy")}
    on_hand = common.stock_on_hand([b["storage_location_id"] for b in common.branches()])
    s14, s30, s60 = _sales_qty(14), _sales_qty(30), _sales_qty(60)
    first_sale = t.sql_one("SELECT min(sale_date) AS d FROM motion.pos_sale")["d"]
    history_days = (common.today() - first_sale).days if first_sale else 0
    out: list[dict] = []
    all_br = common.branches()
    for b in branches:
        for pid, it in items.items():
            stock = on_hand.get((b["storage_location_id"], pid), ZERO)
            vel = (s14.get((b["id"], pid), ZERO) / 14)
            lead = Decimal(policy.get(pid, {}).get("lead_time_days") or 3)
            # 1. Reorder: cover below lead time + 2 days safety
            if vel > 0 and stock / vel < lead + 2:
                cover = (stock / vel).quantize(Decimal("0.1"))
                suggest = max(ZERO, (vel * 14 - stock)).quantize(Decimal("1") if not it["sold_by_weight"] else Decimal("0.1"))
                out.append(_ins(f"reorder:{b['id']}:{pid}", "reorder", "high" if cover < lead else "medium",
                                f"{it['name']} may run out at {b['name']} in ~{cover} days",
                                f"Sells {vel.quantize(Decimal('0.01'))} {it['uom']}/day (14-day average); {stock} on hand; "
                                f"supplier lead time {lead} days.",
                                {"on_hand": stock, "daily_velocity_14d": vel.quantize(Decimal("0.001")), "days_of_cover": cover,
                                 "lead_time_days": lead, "window": "last 14 days of POS sales"},
                                b["code"], {"type": "draft_requisition", "product_id": pid, "branch_id": b["id"],
                                            "quantity": str(suggest), "supplier_id": it["preferred_supplier"]},
                                "medium" if history_days >= 14 else "low"))
            # 2. Slow-moving / dead stock (needs enough history to be meaningful)
            if stock > 0 and history_days >= 30 and not s30.get((b["id"], pid)):
                dead = history_days >= 60 and not s60.get((b["id"], pid))
                value = common.money(stock * it["cost_price"])
                out.append(_ins(f"{'dead' if dead else 'slow'}:{b['id']}:{pid}", "dead_stock" if dead else "slow_moving",
                                "medium" if dead else "low",
                                f"{it['name']}: no sales at {b['name']} in {'60' if dead else '30'} days",
                                f"{stock} {it['uom']} on hand worth about UGX {value:,} at cost tied up.",
                                {"on_hand": stock, "stock_value_at_cost": value, "window": f"last {'60' if dead else '30'} days"},
                                b["code"], {"type": "review_pricing_or_transfer", "product_id": pid}))
            # 3. Branch imbalance -> transfer suggestion
            if vel > 0 and stock / vel < 5:
                for other in all_br:
                    if other["id"] == b["id"] or not actor.can_branch(other["id"]):
                        continue  # H1: never reveal or suggest from branches outside scope
                    o_stock = on_hand.get((other["storage_location_id"], pid), ZERO)
                    o_vel = s14.get((other["id"], pid), ZERO) / 14
                    o_cover = (o_stock / o_vel) if o_vel else (Decimal(999) if o_stock > 0 else ZERO)
                    if o_cover > 30 and o_stock > vel * 7:
                        qty = (vel * 7).quantize(Decimal("1"))
                        out.append(_ins(f"imbalance:{other['id']}:{b['id']}:{pid}", "transfer_suggestion", "medium",
                                        f"Move {it['name']} from {other['name']} to {b['name']}",
                                        f"{b['name']} has {(stock / vel).quantize(Decimal('0.1'))} days of cover; "
                                        f"{other['name']} has {'>999' if o_cover >= 999 else o_cover.quantize(Decimal('0.1'))} days.",
                                        {"to_on_hand": stock, "to_daily_velocity": vel.quantize(Decimal("0.001")),
                                         "from_on_hand": o_stock, "window": "last 14 days"},
                                        b["code"], {"type": "draft_transfer", "from_branch_id": other["id"],
                                                    "to_branch_id": b["id"], "product_id": pid, "quantity": str(qty)}))
                        break
        # 4. Near-expiry batches (estimated FEFO remaining)
        for pid, it in items.items():
            if not policy.get(pid, {}).get("perishable"):
                continue
            for batch in catalog.batches_for(pid, [b["id"]]):
                if batch["expiry_date"] and batch["estimated_remaining"] > 0 and \
                        batch["expiry_date"] <= common.today() + dt.timedelta(days=5):
                    expired = batch["expiry_date"] < common.today()
                    out.append(_ins(f"expiry:{batch['id']}", "near_expiry", "high" if expired else "medium",
                                    f"{it['name']} batch {batch['lot_ref'] or batch['id']} {'expired' if expired else 'expires'} "
                                    f"{batch['expiry_date'].isoformat()} at {b['name']}",
                                    f"Estimated {batch['estimated_remaining']} {it['uom']} remaining (FEFO estimate — "
                                    "till sales do not record batches; verify on the shelf).",
                                    {"estimated_remaining": batch["estimated_remaining"], "expiry_date": batch["expiry_date"],
                                     "received_qty": batch["received_qty"]}, b["code"],
                                    {"type": "markdown_or_writeoff", "product_id": pid}, "low"))
    b_ids = [b["id"] for b in branches]
    # 5. POS review flags (not accusations)
    since = common.today() - dt.timedelta(days=7)
    per_cashier = t.sql("""SELECT s.cashier_id, u.full_name, s.branch_id,
                                  count(*) FILTER (WHERE kind='sale') AS sales, count(*) FILTER (WHERE kind='refund') AS refunds,
                                  COALESCE(sum(discount_total),0) AS discounts, COALESCE(sum(total) FILTER (WHERE kind='sale'),0) AS gross
                           FROM motion.pos_sale s JOIN motion.app_user u ON u.id=s.cashier_id
                           WHERE s.sale_date >= %s AND s.branch_id = ANY(%s) GROUP BY 1,2,3""", (since, b_ids))
    ratios = [r["discounts"] / r["gross"] for r in per_cashier if r["gross"]]
    median = Decimal(str(statistics.median(ratios))) if ratios else ZERO
    codes = {b["id"]: b["code"] for b in common.branches()}
    for r in per_cashier:
        if r["refunds"] >= 2:
            out.append(_ins(f"refunds:{r['cashier_id']}:{since}", "pos_review", "medium",
                            f"{r['refunds']} refunds by {r['full_name']} in 7 days",
                            "Review flag only: check refund reasons and approvals. Not an accusation.",
                            {"refunds": r["refunds"], "sales": r["sales"], "window": "last 7 days"}, codes[r["branch_id"]],
                            {"type": "open_audit", "filter": {"actor": r["cashier_id"], "action": "pos.refund"}}))
        ratio = (r["discounts"] / r["gross"]) if r["gross"] else ZERO
        if r["gross"] and median and ratio > median * 3 and r["discounts"] > 5000:
            out.append(_ins(f"discounts:{r['cashier_id']}:{since}", "pos_review", "low",
                            f"Discounts by {r['full_name']} are {(ratio * 100).quantize(Decimal('0.1'))}% of sales",
                            f"Median across cashiers is {(median * 100).quantize(Decimal('0.01'))}%. Review flag only.",
                            {"discounts": r["discounts"], "gross_sales": r["gross"], "window": "last 7 days"},
                            codes[r["branch_id"]]))
    for r in t.sql("""SELECT s.cashier_id, u.full_name, s.branch_id, count(*) AS n, sum(s.variance) AS total
                      FROM motion.shift s JOIN motion.app_user u ON u.id=s.cashier_id
                      WHERE s.variance <> 0 AND s.closed_at > now() - interval '30 days' AND s.branch_id = ANY(%s)
                      GROUP BY 1,2,3""", (b_ids,)):
        out.append(_ins(f"variance:{r['cashier_id']}", "cash_variance", "medium" if r["n"] >= 2 else "low",
                        f"{r['n']} shift cash variance(s) for {r['full_name']} (net UGX {r['total']:,})",
                        "Counted cash differed from expected cash. Reconcile with the branch manager.",
                        {"shifts_with_variance": r["n"], "net_variance": r["total"], "window": "last 30 days"},
                        codes[r["branch_id"]]))
    for r in t.sql("""SELECT s.id, u.full_name, s.branch_id, s.opened_at FROM motion.shift s JOIN motion.app_user u ON u.id=s.cashier_id
                      WHERE s.status='open' AND s.opened_at < now() - interval '14 hours' AND s.branch_id = ANY(%s)""", (b_ids,)):
        out.append(_ins(f"interrupted:{r['id']}", "data_quality", "high", f"Shift left open since {r['opened_at']:%d %b %H:%M} ({r['full_name']})",
                        "An interrupted shift blocks the register; a manager should count the drawer and close it.",
                        {"opened_at": r["opened_at"]}, codes[r["branch_id"]], {"type": "close_shift", "shift_id": r["id"]}))
    # 6. Data quality: edge sync freshness, negative stock, stale draft bills
    for r in t.sql("""SELECT id, name, branch_id, last_seen_at FROM motion.edge_device WHERE revoked_at IS NULL
                      AND (last_seen_at IS NULL OR last_seen_at < now() - interval '30 minutes') AND branch_id = ANY(%s)""", (b_ids,)):
        out.append(_ins(f"edge_stale:{r['id']}", "data_quality", "high", f"Branch edge '{r['name']}' has not synced recently",
                        "Sales captured offline may not be in central reports yet.",
                        {"last_seen_at": r["last_seen_at"]}, codes[r["branch_id"]]))
    neg = [(codes.get(bid), pid) for (loc, pid), q in on_hand.items() if q < 0
           for bid in [next((b["id"] for b in branches if b["storage_location_id"] == loc), None)] if bid]
    if neg:
        out.append(_ins("negative_stock", "data_quality", "medium", f"{len(neg)} product lines show negative stock",
                        "Usually offline oversell or unrecorded receipts. Count and reconcile.", {"lines": len(neg)}))
    # 7. Executive narrative grounded in verified metrics
    out.extend(_executive(actor, b_ids))
    acks = {r["insight_key"]: r for r in t.sql("SELECT * FROM motion.insight_ack")}
    for i in out:
        a = acks.get(i["key"])
        i["status"] = a["status"] if a else "new"
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda i: (i["status"] != "new", order[i["severity"]], i["kind"]))


def _week_metrics(b_ids, d0, d1):
    return t.sql_one("""SELECT COALESCE(sum(CASE WHEN a.code LIKE '4.%%' THEN l.credit-l.debit END),0) AS sales,
                               COALESCE(sum(CASE WHEN a.code='5.1.1.01' THEN l.debit-l.credit END),0) AS cogs
                        FROM account_move_line l JOIN account_move m ON m.id=l.move JOIN account_account a ON a.id=l.account
                        WHERE m.state='posted' AND m.date BETWEEN %s AND %s AND m.branch = ANY(%s)""", (d0, d1, b_ids))


def _executive(actor: Principal, b_ids: list[int]) -> list[dict]:
    if not actor.has("finance.view"):
        return []
    today = common.today()
    this0, this1 = today - dt.timedelta(days=6), today
    last0, last1 = today - dt.timedelta(days=13), today - dt.timedelta(days=7)
    a, b = _week_metrics(b_ids, this0, this1), _week_metrics(b_ids, last0, last1)
    if not b["sales"]:
        return [_ins("exec:none", "executive", "low", "Not enough data for a week-on-week comparison",
                     "At least two full weeks of posted sales are needed.", {}, confidence="n/a")]
    ch = ((a["sales"] - b["sales"]) / b["sales"] * 100).quantize(Decimal("0.1"))
    gm_a = ((a["sales"] - a["cogs"]) / a["sales"] * 100).quantize(Decimal("0.1")) if a["sales"] else ZERO
    gm_b = ((b["sales"] - b["cogs"]) / b["sales"] * 100).quantize(Decimal("0.1"))
    refunds = t.sql_one("""SELECT count(*) AS n, COALESCE(-sum(total),0) AS v FROM motion.pos_sale WHERE kind='refund'
                           AND sale_date BETWEEN %s AND %s AND branch_id = ANY(%s)""", (this0, this1, b_ids))
    direction = "up" if ch >= 0 else "down"
    text = (f"Net sales (ex-VAT) were UGX {a['sales']:,} over {this0:%d %b}–{this1:%d %b}, {direction} {abs(ch)}% on the previous 7 days "
            f"(UGX {b['sales']:,}). Gross margin moved from {gm_b}% to {gm_a}%. {refunds['n']} refund(s) worth UGX {refunds['v']:,} "
            "were processed in the current week.")
    return [_ins(f"exec:{this1}", "executive", "low", "Week-on-week summary", text,
                 {"net_sales_this_week": a["sales"], "net_sales_prev_week": b["sales"], "gross_margin_this": gm_a,
                  "gross_margin_prev": gm_b, "refunds": refunds["n"], "window": f"{last0} to {this1}",
                  "source": "posted ledger (revenue 4.*, COGS 5.1.1.01) + POS refunds"}, confidence="high")]


def acknowledge(actor: Principal, key: str, status: str, note: str | None) -> None:
    actor.require("insights.view", action="insights.ack")
    if status not in {"acknowledged", "dismissed"}:
        raise Invalid("Unknown status.")
    # H1: only insights the actor can currently see (i.e. within branch scope) can be resolved.
    if key not in {i["key"] for i in generate(actor)}:
        raise Forbidden("That insight is not in your branch scope.", action="insights.ack", target_type="insight",
                        target_id=key)
    t.execute("""INSERT INTO motion.insight_ack (insight_key, status, note, by_user) VALUES (%s,%s,%s,%s)
                 ON CONFLICT (insight_key) DO UPDATE SET status=EXCLUDED.status, note=EXCLUDED.note, by_user=EXCLUDED.by_user, at=now()""",
              (key, status, note, actor.user_id))
    record(actor=actor, action=f"insights.{status}", target_type="insight", target_id=key, reason=note)


def ask(actor: Principal, question: str) -> dict:
    """'Ask Motion' — deterministic intent routing over the same scoped tools (no LLM, no free SQL)."""
    actor.require("insights.view", action="insights.ask")
    q = (question or "").lower()
    insights = generate(actor)
    record(actor=actor, action="insights.ask", target_type="question", reason=question[:300])
    def pick(kinds):
        return [i for i in insights if i["kind"] in kinds]
    if any(w in q for w in ("run out", "stock out", "reorder", "low stock")):
        hits = pick({"reorder"})
        answer = f"{len(hits)} product/branch combinations are projected to run out within their lead time." if hits else \
            "No product is projected to run out within its supplier lead time."
    elif any(w in q for w in ("variance", "short", "over", "cash")):
        hits = pick({"cash_variance"})
        answer = f"{len(hits)} cashier(s) had shift cash variances in the last 30 days." if hits else "No shift cash variances in the last 30 days."
    elif any(w in q for w in ("margin", "profit", "sales", "week")):
        hits = pick({"executive"})
        answer = hits[0]["detail"] if hits else "Not enough data."
    elif any(w in q for w in ("expire", "expiry", "fresh")):
        hits = pick({"near_expiry"})
        answer = f"{len(hits)} perishable batch(es) expire within 5 days (estimated)." if hits else "No batches near expiry."
    elif any(w in q for w in ("slow", "dead", "not selling")):
        hits = pick({"slow_moving", "dead_stock"})
        answer = f"{len(hits)} slow-moving or dead stock lines." if hits else "No slow-moving stock detected."
    elif any(w in q for w in ("transfer", "imbalance", "move stock")):
        hits = pick({"transfer_suggestion"})
        answer = f"{len(hits)} transfer suggestion(s) to rebalance stock." if hits else "No branch imbalance detected."
    else:
        hits = []
        answer = ("I can answer questions about stock-outs, slow or expiring stock, transfers, cash variances, and "
                  "sales/margin trends. I only use data you are authorised to see.")
    return {"answer": answer, "evidence": hits[:10], "method": f"{METHOD} intent routing (no language model)",
            "llm_enabled": False, "caveat": "Answers come from rule-based analysis of posted data and are not financial advice."}
