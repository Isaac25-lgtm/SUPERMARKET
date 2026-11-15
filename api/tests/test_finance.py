"""Reporting correctness: reconciliation, definitions, break-even and payback."""
from __future__ import annotations

from decimal import Decimal

from conftest import new_key, q, q1

D = Decimal


def test_trial_balance_and_every_entry_balanced(login):
    tb = login("acc.amina").get("/api/finance/trial-balance").json()
    assert tb["balanced"] is True
    assert q1("SELECT count(*) AS n FROM (SELECT move FROM account_move_line GROUP BY move HAVING sum(debit) <> sum(credit)) x")["n"] == 0


def test_branch_pnl_reconciles_to_consolidated(login):
    for period in ("today", "month", "fytd"):
        p = login("hm.owner").get("/api/finance/pnl", period=period).json()
        assert p["reconciles"] is True
        for key in ("net_sales", "cogs", "gross_profit", "operating_expenses", "operating_profit"):
            assert sum(D(b[key]) for b in p["branches"]) == D(p["consolidated"][key]), (period, key)


def test_pnl_matches_independent_sql(login):
    p = login("hm.owner").get("/api/finance/pnl", period="fytd").json()
    row = q1("""SELECT sum(CASE WHEN a.code LIKE '4.%%' THEN l.credit - l.debit ELSE 0 END) AS sales,
                       sum(CASE WHEN a.code = '5.1.1.01' THEN l.debit - l.credit ELSE 0 END) AS cogs
                FROM account_move_line l JOIN account_move m ON m.id=l.move JOIN account_account a ON a.id=l.account
                WHERE m.state='posted' AND m.date >= date_trunc('year', now())::date""")
    assert D(p["consolidated"]["net_sales"]) == D(row["sales"] or 0)
    assert D(p["consolidated"]["gross_profit"]) == D(row["sales"] or 0) - D(row["cogs"] or 0)


def test_profit_definitions_are_distinct(login):
    c = login("hm.owner").get("/api/finance/pnl", period="fytd").json()["consolidated"]
    assert D(c["gross_profit"]) == D(c["net_sales"]) - D(c["cogs"])
    assert D(c["operating_profit"]) == D(c["gross_profit"]) - D(c["shrinkage"]) - D(c["operating_expenses"]) - D(c["other_operating"])


def test_break_even_formula(login):
    be = login("hm.owner").get("/api/finance/break-even", period="fytd").json()
    if be["status"] != "ok":
        assert be["status"] in {"not_enough_data", "negative_margin"}
        return
    cm = (D(be["net_sales"]) - D(be["variable_costs"])) / D(be["net_sales"])
    assert abs(D(be["break_even_sales"]) - D(be["fixed_costs"]) / cm) <= 1


def test_capital_recovery_is_honest_about_history(login):
    r = login("hm.owner").get("/api/finance/capital-recovery").json()
    # Fixture DB has an investment but almost no cash history: must say so, not invent a date.
    assert r["status"] in {"not_enough_data", "not_recovering", "projected"}
    if r["weeks_of_data"] < 8:
        assert r["status"] == "not_enough_data" and "estimated_recovery_date" not in r


def test_branch_manager_sees_only_own_branch_pnl(login, ids):
    p = login("bm.sarah").get("/api/finance/pnl", period="fytd").json()
    assert {b["code"] for b in p["branches"]} <= {"NTH"}


def test_export_is_audited(login):
    r = login("acc.amina").get("/api/reports/pnl.csv", period="month")
    assert r.status_code == 200 and r.text.startswith("branch,")
    assert q("SELECT 1 FROM motion.audit_event WHERE action='reports.export.pnl' AND actor_username='acc.amina'")


def test_dashboards_differ_by_role(login):
    keys = {}
    for u in ("hm.owner", "gm.grace", "acc.amina", "ops.david", "inv.ruth", "bm.joseph"):
        r = login(u).get("/api/dashboard")
        assert r.status_code == 200, (u, r.text)
        keys[u] = set(r.json())
    assert "recovery" in keys["hm.owner"] and "recovery" not in keys["acc.amina"]
    assert "pipeline" in keys["ops.david"] and "payables" in keys["acc.amina"] and "shifts" in keys["bm.joseph"]
    assert len({frozenset(v) for v in keys.values()}) >= 5


def test_insights_are_evidence_linked(login):
    ins = login("hm.owner").get("/api/insights").json()["insights"]
    for i in ins:
        assert i["method"] == "rules-v1" and "evidence" in i and i["severity"] in {"high", "medium", "low"}
    ans = login("hm.owner").post("/api/insights/ask", {"question": "Which items may run out this week?"}).json()
    assert ans["llm_enabled"] is False and "answer" in ans


def test_bank_deposit_moves_cash_from_safe_to_bank(login, ids):
    from conftest import account_balance
    sth = ids["branch"]["STH"]
    assert login("cash.sth1").post("/api/finance/bank-deposits", {"branch_id": sth, "amount": "1000", "date": "2026-11-15",
                                                                   "reference": "SLIP-1", "idempotency_key": new_key()}).status_code == 403
    acc = login("acc.amina")
    safe = account_balance("1.1.1.02", sth)
    too_much = acc.post("/api/finance/bank-deposits", {"branch_id": sth, "amount": str(safe + 1000), "date": "2026-11-15",
                                                       "reference": "SLIP-2", "idempotency_key": new_key()})
    assert too_much.status_code == 422  # cannot bank more than the safe holds
    if safe >= 1000:
        bank0 = account_balance("1.1.1.03", sth)
        r = acc.post("/api/finance/bank-deposits", {"branch_id": sth, "amount": "1000", "date": "2026-11-15", "reference": "SLIP-3", "idempotency_key": new_key()})
        assert r.status_code == 201, r.text
        assert account_balance("1.1.1.03", sth) - bank0 == 1000
        assert safe - account_balance("1.1.1.02", sth) == 1000
