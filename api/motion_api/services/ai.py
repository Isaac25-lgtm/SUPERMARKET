"""Ask Motion AI: a supermarket assistant backed by DeepSeek (OpenAI-compatible API).

Governance (brief §9):
* The model never touches the database. It can only call the read-only tools
  below. Each tool re-checks the asker's permission and branch scope inside its
  own short read-only transaction, exactly like the normal API.
* Only aggregated business figures leave the system (totals, stock levels,
  alerts). No passwords, sessions, payment references or customer data.
* The assistant cannot act: no posting, approving, transferring, re-pricing or
  refunding. Answers cite the tools and periods used.
* Every question is logged in motion.ai_query and audited; per-user hourly limit.
* If the provider is disabled or fails, the deterministic rule-based answer is used.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from decimal import Decimal
from typing import Any

import httpx

from .. import tryton as t
from ..audit import record
from ..errors import Invalid, RateLimited
from ..security import Principal
from ..settings import get_settings
from . import approvals, catalog, common, finance, insights

logger = logging.getLogger("motion.ai")
MAX_TOOL_ROUNDS = 5
# Tests inject an httpx.MockTransport here so the suite never calls the real provider.
TRANSPORT: httpx.BaseTransport | None = None
PERIODS = ["today", "yesterday", "week", "month", "last30", "fytd"]


def _jsonable(o: Any) -> Any:
    if isinstance(o, Decimal):
        return float(o) if o != o.to_integral_value() else int(o)
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    return o


def _branch_id(actor: Principal, code: str | None) -> int | None:
    if not code:
        return None
    for b in common.branches():
        if b["code"].lower() == code.strip().lower() or b["name"].lower() == code.strip().lower():
            actor.require_branch(b["id"], action="ai.tool")
            return b["id"]
    raise Invalid(f"Unknown branch '{code}'.")


def _clean_name(n: str | None) -> str | None:
    return n.split(" (TEST")[0] if n else n


# ---------------------------------------------------------------- tools (read-only, scoped)

def tool_list_branches(actor: Principal) -> dict:
    return {"branches": [{"code": b["code"], "name": b["name"]} for b in common.branches() if actor.can_branch(b["id"])]}


def tool_sales_summary(actor: Principal, period: str = "today", branch_code: str | None = None) -> dict:
    actor.require_any("finance.view", "dashboard.branch")
    d0, d1, label = finance.period_bounds(period)
    bid = _branch_id(actor, branch_code)
    ids = [bid] if bid else actor.scoped_branch_ids(common.branch_ids())
    k = finance.sales_kpis(actor, d0=d0, d1=d1, branch_ids=ids)
    return {"period": label, "from": d0, "to": d1, "branches": branch_code or "all in your scope",
            "receipts": k["sales_count"], "gross_sales_incl_vat": k["gross_sales"], "refunds": k["refunds"],
            "discounts": k["discounts"], "vat": k["vat"], "average_basket": k["avg_basket"],
            "by_payment_method": k["by_method"], "by_category": k["by_category"][:8],
            "top_products": k["top_products"][:8], "by_hour": k["by_hour"]}


def tool_profit_and_loss(actor: Principal, period: str = "month", branch_code: str | None = None) -> dict:
    p = finance.profit_and_loss(actor, period=period, branch_id=_branch_id(actor, branch_code))
    keys = ("net_sales", "cogs", "gross_profit", "gross_margin_pct", "shrinkage", "operating_expenses",
            "other_operating", "operating_profit")
    return {"period": p["period"]["label"], "from": p["period"]["from"], "to": p["period"]["to"],
            "consolidated": {k: p["consolidated"][k] for k in keys},
            "branches": [{"branch": b["name"], **{k: b[k] for k in keys}} for b in p["branches"]],
            "notes": "Net sales exclude VAT. Operating profit is before income tax. Purchase-clearing timing excluded."}


def tool_sales_trend(actor: Principal, days: int = 14, branch_code: str | None = None) -> dict:
    days = max(7, min(int(days), 90))
    s = finance.daily_series(actor, days=days, branch_id=_branch_id(actor, branch_code))
    return {"days": days, "daily": [{"day": r["day"], "net_sales": r["net_sales"], "gross_profit": r["gross_profit"]} for r in s]}


def tool_find_products(actor: Principal, query: str, branch_code: str | None = None) -> dict:
    res = catalog.list_products(actor, branch_id=_branch_id(actor, branch_code), q=query[:60])
    return {"matches": [{"name": i["name"], "shelf_price": i["shelf_price"], "unit": i["uom"],
                         "sold_by_weight": i["sold_by_weight"], "stock_by_branch": i["stock"],
                         "reorder_point": i["reorder_point"], "low_at": i.get("low_at", [])}
                        for i in res["items"][:8]], "total_matches": len(res["items"])}


def tool_stock_alerts(actor: Principal, kind: str = "all") -> dict:
    items = insights.generate(actor)
    if kind != "all":
        items = [i for i in items if i["kind"] == kind]
    return {"alerts": [{"kind": i["kind"], "severity": i["severity"], "branch": i["branch"], "title": i["title"],
                        "detail": i["detail"], "evidence": i["evidence"]} for i in items[:15]],
            "method": insights.METHOD, "total": len(items)}


def tool_cash_position(actor: Principal) -> dict:
    c = finance.cash_position(actor)
    return {"accounts": [{"account": a["name"], "balance": a["balance"]} for a in c["accounts"]], "total": c["total"],
            "note": c["note"]}


def tool_pending_approvals(actor: Principal) -> dict:
    box = approvals.inbox(actor)

    def brief(r):
        return {"ref": r["ref"], "type": r["action_label"], "title": r["title"], "amount": r["amount"],
                "branch": r["branch_name"], "status": r["status_label"], "overdue": r.get("overdue", False)}
    return {"waiting_for_you": [brief(r) for r in box["actionable"][:15]],
            "other_pending": [brief(r) for r in box["watching"][:10]]}


def tool_break_even(actor: Principal, period: str = "last30") -> dict:
    return finance.break_even(actor, period=period)


def tool_shift_variances(actor: Principal) -> dict:
    actor.require_any("pos.shift.reconcile", "finance.view")
    rows = t.sql("""SELECT s.id, b.code AS branch, p.name AS register, u.full_name AS cashier, s.closed_at, s.variance, s.status
                    FROM motion.shift s JOIN motion_branch b ON b.id=s.branch_id JOIN sale_point p ON p.id=s.register_id
                    JOIN motion.app_user u ON u.id=s.cashier_id
                    WHERE s.variance <> 0 AND s.closed_at > now() - interval '30 days' AND (%s OR s.branch_id = ANY(%s))
                    ORDER BY s.closed_at DESC LIMIT 20""", (actor.all_branches, sorted(actor.branch_ids)))
    return {"window": "last 30 days", "shifts": [{**r, "cashier": _clean_name(r["cashier"])} for r in rows]}


TOOLS: dict[str, tuple[Any, str, dict]] = {
    "list_branches": (tool_list_branches, "List the branches the user may see.", {"type": "object", "properties": {}}),
    "get_sales_summary": (tool_sales_summary, "Sales totals, refunds, discounts, VAT, basket size, payment methods, "
                          "categories, top products and hourly pattern for a period (POS figures, VAT-inclusive).",
                          {"type": "object", "properties": {"period": {"type": "string", "enum": PERIODS},
                                                            "branch_code": {"type": "string", "description": "e.g. CEN, NTH, STH; omit for all"}},
                           "required": ["period"]}),
    "get_profit_and_loss": (tool_profit_and_loss, "Profit & loss from the posted ledger by branch: net sales (ex-VAT), "
                            "COGS, gross profit and margin, shrinkage, operating expenses, operating profit.",
                            {"type": "object", "properties": {"period": {"type": "string", "enum": PERIODS},
                                                              "branch_code": {"type": "string"}}, "required": ["period"]}),
    "get_sales_trend": (tool_sales_trend, "Daily net sales and gross profit for the last N days (7-90).",
                        {"type": "object", "properties": {"days": {"type": "integer"}, "branch_code": {"type": "string"}}}),
    "find_products": (tool_find_products, "Search products by name/SKU/barcode; returns price, stock per branch and reorder status.",
                      {"type": "object", "properties": {"query": {"type": "string"}, "branch_code": {"type": "string"}},
                       "required": ["query"]}),
    "get_stock_alerts": (tool_stock_alerts, "Rule-based alerts with evidence: reorder (may run out), near_expiry, "
                         "slow_moving, dead_stock, transfer_suggestion, pos_review, cash_variance, data_quality, executive.",
                         {"type": "object", "properties": {"kind": {"type": "string", "enum": [
                             "all", "reorder", "near_expiry", "slow_moving", "dead_stock", "transfer_suggestion",
                             "pos_review", "cash_variance", "data_quality", "executive"]}}}),
    "get_cash_position": (tool_cash_position, "Balances of till cash, safe, bank and mobile-money/card clearing accounts.",
                          {"type": "object", "properties": {}}),
    "get_pending_approvals": (tool_pending_approvals, "Approval requests waiting for this user and other pending ones.",
                              {"type": "object", "properties": {}}),
    "get_break_even": (tool_break_even, "Operating break-even for a period with its assumptions.",
                       {"type": "object", "properties": {"period": {"type": "string", "enum": PERIODS}}}),
    "get_shift_variances": (tool_shift_variances, "Cashier shifts with cash over/short in the last 30 days.",
                            {"type": "object", "properties": {}}),
}


def _tool_specs() -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": p}} for n, (_, d, p) in TOOLS.items()]


def _run_tool(actor: Principal, name: str, raw_args: str) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return {"error": "invalid arguments"}
    fn = TOOLS[name][0]
    try:
        out = t.run(lambda: fn(actor, **args), user=0, readonly=True)
        return _jsonable(out)
    except TypeError:
        return {"error": "invalid arguments for this tool"}
    except Exception as exc:  # noqa: BLE001 - permission/validation errors are returned to the model
        message = getattr(exc, "message", None) or str(exc)
        return {"error": message[:300]}


def _system_prompt(actor: Principal) -> str:
    s = get_settings()
    branches = [b["name"] for b in t.run(common.branches, readonly=True) if actor.can_branch(b["id"])]
    today = t.run(common.today, readonly=True)
    return (
        f"You are Motion AI, the assistant inside {s.brand_name}'s supermarket management system (by Motion Imprints). "
        f"Today is {today.isoformat()} (Africa/Kampala). Currency is UGX (no decimals). "
        f"You are talking to {actor.full_name.split(' (TEST')[0]}, roles: {', '.join(sorted(actor.roles))}; "
        f"branches they may see: {', '.join(branches) or 'none'}.\n"
        "Rules:\n"
        "- Answer ONLY from tool results. Never invent figures, products, people or events. If a tool returns an error "
        "or no data, say so plainly ('not enough data' is a valid answer).\n"
        "- You cannot take actions (no approving, posting, transferring, pricing, refunding). Suggest which screen to use.\n"
        "- Be concise and practical for a busy manager: short paragraphs or bullets, key numbers formatted like "
        "UGX 1,234,500, and state the period and branches the figures cover.\n"
        "- Distinguish gross sales (incl. VAT, POS) from net sales (ex-VAT, ledger), gross profit, operating profit and cash.\n"
        "- Alerts about cashiers are review prompts, not accusations.\n"
        + ("- All data in this system is synthetic TEST DATA; mention that if the user seems to treat it as real.\n"
           if s.demo_mode else "")
    )


def _rate_limit(actor: Principal) -> None:
    limit = get_settings().ai_hourly_limit
    n = t.run(lambda: t.sql_one("""SELECT count(*) AS n FROM motion.ai_query WHERE user_id=%s
                                   AND at > now() - interval '1 hour'""", (actor.user_id,))["n"], readonly=True)
    if n >= limit:
        raise RateLimited(f"You have reached {limit} AI questions this hour. Please try again later.")


def _log(actor: Principal, question: str, tools: list, answer: str | None, status: str, model: str | None,
         usage: dict, latency_ms: int) -> None:
    def work():
        t.execute("""INSERT INTO motion.ai_query (user_id, question, tools, answer, model, provider, prompt_tokens,
                     completion_tokens, latency_ms, status) VALUES (%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)""",
                  (actor.user_id, question[:2000], json.dumps(tools, default=str)[:20000], (answer or "")[:8000], model,
                   "deepseek" if model else None, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                   latency_ms, status))
        record(actor=actor, action="ai.ask", target_type="ai_query", reason=question[:300],
               after={"tools": [x["tool"] for x in tools], "status": status, "model": model})
    t.run(work, user=0)


def chat(actor: Principal, message: str, history: list[dict] | None = None) -> dict:
    """One assistant turn. ``history`` is the prior visible conversation (text only)."""
    actor.require("insights.view", action="ai.ask")
    question = (message or "").strip()
    if len(question) < 2:
        raise Invalid("Type a question.")
    if len(question) > 1000:
        raise Invalid("Please keep questions under 1,000 characters.")
    _rate_limit(actor)
    s = get_settings()
    started = time.monotonic()
    if not s.ai_llm_enabled:
        fb = t.run(lambda: insights.ask(actor, question), user=0)
        return {"answer": fb["answer"], "evidence": [], "llm": False, "model": None,
                "note": "AI assistant is switched off; showing the rule-based answer."}
    msgs: list[dict] = [{"role": "system", "content": _system_prompt(actor)}]
    for h in (history or [])[-8:]:
        if h.get("role") in {"user", "assistant"} and isinstance(h.get("content"), str):
            msgs.append({"role": h["role"], "content": h["content"][:2000]})
    msgs.append({"role": "user", "content": question})
    used: list[dict] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    answer = None
    try:
        with httpx.Client(base_url=s.ai_base_url, timeout=45, transport=TRANSPORT,
                          headers={"Authorization": f"Bearer {s.ai_api_key}", "Content-Type": "application/json"}) as client:
            for _ in range(MAX_TOOL_ROUNDS):
                r = client.post("/chat/completions", json={"model": s.ai_model, "messages": msgs, "tools": _tool_specs(),
                                                          "temperature": 0.2, "max_tokens": 900})
                r.raise_for_status()
                data = r.json()
                for k in usage:
                    usage[k] += (data.get("usage") or {}).get(k, 0) or 0
                msg = data["choices"][0]["message"]
                calls = msg.get("tool_calls") or []
                if not calls:
                    answer = (msg.get("content") or "").strip()
                    break
                msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    name = c["function"]["name"]
                    args = c["function"].get("arguments") or "{}"
                    result = _run_tool(actor, name, args)
                    summary = "error: " + result["error"] if isinstance(result, dict) and "error" in result else "ok"
                    used.append({"tool": name, "args": json.loads(args) if args.strip().startswith("{") else {}, "result": summary})
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=str)[:12000]})
            if not answer:
                answer = "I could not finish that analysis. Please ask a narrower question (e.g. one branch or one period)."
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("AI provider failed: %s", type(exc).__name__)
        fb = t.run(lambda: insights.ask(actor, question), user=0)
        latency = int((time.monotonic() - started) * 1000)
        _log(actor, question, used, fb["answer"], "fallback", s.ai_model, usage, latency)
        return {"answer": fb["answer"], "evidence": [], "llm": False, "model": None,
                "note": "The AI service did not respond; showing the rule-based answer instead."}
    latency = int((time.monotonic() - started) * 1000)
    _log(actor, question, used, answer, "answered", s.ai_model, usage, latency)
    return {"answer": answer, "evidence": used, "llm": True, "model": s.ai_model, "latency_ms": latency,
            "note": "AI-generated from your authorised data. Check important figures in the reports."}


def status(actor: Principal) -> dict:
    actor.require("insights.view")
    s = get_settings()
    return {"enabled": s.ai_llm_enabled, "model": s.ai_model if s.ai_llm_enabled else None,
            "provider": "DeepSeek" if s.ai_llm_enabled else None, "hourly_limit": s.ai_hourly_limit,
            "suggestions": ["How are sales this week compared with last week?", "Which items may run out soon?",
                            "What was our gross margin this month by branch?", "Any products close to expiry?",
                            "Which approvals are waiting for me?", "How much cash do we hold right now?"]}
