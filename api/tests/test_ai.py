"""Ask Motion AI: tool loop, branch scope, data minimisation, fallback, rate limit and logging.

The DeepSeek provider is replaced by an httpx.MockTransport, so the suite never
sends anything to the real service.
"""
from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from conftest import q1
from motion_api.services import ai
from motion_api.settings import get_settings


class FakeProvider:
    """Scripted chat-completions endpoint. Each script step is either a list of
    (tool_name, args) calls or a final answer string."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        assert request.headers["Authorization"] == "Bearer test-key"
        step = self.script.pop(0)
        if isinstance(step, int):
            return httpx.Response(step, json={"error": "boom"})
        usage = {"prompt_tokens": 100, "completion_tokens": 20}
        if isinstance(step, str):
            msg = {"role": "assistant", "content": step}
        else:
            msg = {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                for i, (n, a) in enumerate(step)]}
        return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": "stop"}], "usage": usage})

    def tool_messages(self) -> list[dict]:
        last = self.requests[-1]["messages"]
        return [json.loads(m["content"]) for m in last if m["role"] == "tool"]


@pytest.fixture()
def provider(monkeypatch):
    def install(script, **overrides):
        fake = FakeProvider(script)
        on = dataclasses.replace(get_settings(), ai_llm_enabled=True, ai_api_key="test-key", **overrides)
        monkeypatch.setattr(ai, "get_settings", lambda: on)
        monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(fake))
        return fake
    return install


def ask(api, message, history=None):
    return api.post("/api/ai/chat", {"message": message, "history": history or []})


def test_tool_loop_answers_from_scoped_data_and_is_logged(provider, login):
    fake = provider([[("get_sales_summary", {"period": "last30"})], "Sales were good (TEST DATA)."])
    owner = login("hm.owner")
    r = ask(owner, "How were sales in the last 30 days?")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["llm"] is True and body["answer"] == "Sales were good (TEST DATA)."
    assert body["evidence"] == [{"tool": "get_sales_summary", "args": {"period": "last30"}, "result": "ok"}]
    result = fake.tool_messages()[0]
    assert {"receipts", "gross_sales_incl_vat", "top_products", "period"} <= set(result)
    # Data minimisation: no per-cashier breakdown, no cost prices, no secrets in what leaves the system.
    sent = json.dumps(fake.requests)
    assert "by_cashier" not in sent and "cost_price" not in sent and "password" not in sent.lower()
    system = fake.requests[0]["messages"][0]["content"]
    assert "Motion AI" in system and "Answer ONLY from tool results" in system
    row = q1("SELECT * FROM motion.ai_query WHERE user_id=%s ORDER BY id DESC LIMIT 1", (owner.me["id"],))
    assert row["status"] == "answered" and row["prompt_tokens"] == 200 and row["tools"][0]["tool"] == "get_sales_summary"
    audit = q1("SELECT * FROM motion.audit_event WHERE action='ai.ask' ORDER BY id DESC LIMIT 1")
    assert audit["actor_id"] == owner.me["id"]


def test_tools_enforce_branch_scope_and_permissions(provider, login):
    fake = provider([[("get_profit_and_loss", {"period": "month", "branch_code": "CEN"}),
                      ("get_profit_and_loss", {"period": "month"}),
                      ("get_cash_position", {})], "Only North is visible to you."])
    sarah = login("bm.sarah")  # North only
    r = ask(sarah, "Compare Central and North profit this month")
    assert r.status_code == 200, r.text
    central, own, cash = fake.tool_messages()
    assert "error" in central and "not assigned" in central["error"]
    assert all("North" in b["branch"] for b in own["branches"])  # never another branch
    assert "accounts" in cash
    # An inventory manager has no finance permission: the tool refuses.
    fake2 = provider([[("get_cash_position", {})], "You do not have access to cash figures."])
    ruth = login("inv.ruth")
    assert ask(ruth, "How much cash do we have?").status_code == 200
    assert "error" in fake2.tool_messages()[0]
    evidence = q1("SELECT tools FROM motion.ai_query WHERE user_id=%s ORDER BY id DESC LIMIT 1", (ruth.me["id"],))["tools"]
    assert evidence[0]["result"].startswith("error")


def test_unknown_tool_and_bad_arguments_are_contained(provider, login):
    fake = provider([[("drop_tables", {}), ("get_sales_summary", {"period": "week", "evil": 1})], "Done."])
    r = ask(login("gm.grace"), "do something odd")
    assert r.status_code == 200
    unknown, bad = fake.tool_messages()
    assert "unknown tool" in unknown["error"] and "invalid arguments" in bad["error"]


def test_provider_failure_falls_back_to_rule_based_answer(provider, login):
    provider([500])
    owner = login("hm.owner")
    r = ask(owner, "Which items may run out soon?")
    assert r.status_code == 200
    body = r.json()
    assert body["llm"] is False and "rule-based" in body["note"] and body["answer"]
    row = q1("SELECT status FROM motion.ai_query WHERE user_id=%s ORDER BY id DESC LIMIT 1", (owner.me["id"],))
    assert row["status"] == "fallback"


def test_disabled_assistant_uses_rule_based_answer(login):
    r = ask(login("hm.owner"), "Which items may run out soon?")
    assert r.status_code == 200 and r.json()["llm"] is False


def test_hourly_rate_limit(provider, login):
    acc = login("acc.amina")
    used = q1("SELECT count(*) AS n FROM motion.ai_query WHERE user_id=%s AND at > now() - interval '1 hour'",
              (acc.me["id"],))["n"]
    provider(["One.", "Two."], ai_hourly_limit=used + 1)
    assert ask(acc, "first question").status_code == 200
    r = ask(acc, "second question")
    assert r.status_code == 429 and "AI questions this hour" in r.json()["message"]


def test_cashier_cannot_use_the_assistant(provider, login):
    provider(["should not be called"])
    from conftest import Api
    r = ask(Api("cash.cen1"), "What are total sales?")
    assert r.status_code == 403


def test_history_is_bounded_and_text_only(provider, login):
    fake = provider(["Fine."])
    history = [{"role": "user", "content": "x" * 3000}, {"role": "assistant", "content": "ok"}] * 6
    r = ask(login("gm.grace"), "and today?", history)
    assert r.status_code == 200
    msgs = fake.requests[0]["messages"]
    assert len(msgs) == 1 + 8 + 1 and all(len(m["content"]) <= 2000 for m in msgs[1:])


def offered(fake) -> set[str]:
    return {t["function"]["name"] for t in fake.requests[0]["tools"]}


def test_model_is_only_offered_tools_the_role_allows(provider, login):
    fake = provider(["ok"])
    ask(login("inv.ruth"), "what can you tell me?")
    tools = offered(fake)
    assert {"find_products", "get_stock_alerts", "get_pending_approvals"} <= tools
    assert not tools & {"get_profit_and_loss", "get_cash_position", "get_payables", "get_break_even"}
    system = fake.requests[0]["messages"][0]["content"]
    assert "Stock & products" in system and "Profit & cash" not in system
    fake = provider(["ok"])
    ask(login("hm.owner"), "what can you tell me?")
    assert {"get_profit_and_loss", "get_cash_position", "get_payables"} <= offered(fake)


def test_answers_link_to_sections_the_user_can_open(provider, login):
    fake = provider([[("get_profit_and_loss", {"period": "month"}), ("find_products", {"query": "sugar"})], "Here you go."])
    body = ask(login("acc.amina"), "margin and sugar stock").json()
    hrefs = [ln["href"] for ln in body["links"]]
    assert "/finance" in hrefs and "/stock" in hrefs
    assert any(h.startswith("/stock/products/") for h in hrefs)
    assert fake.tool_messages()[1]["matches"], "fixture catalogue has sugar"
    # A tool the role lacks yields neither data nor a link.
    provider([[("get_cash_position", {})], "No access."])
    body = ask(login("inv.ruth"), "cash?").json()
    assert body["links"] == []
    assert body["evidence"][0]["result"].startswith("error")


def test_suggestions_follow_the_role(login):
    ruth = login("inv.ruth").get("/api/ai/status").json()["suggestions"]
    owner = login("hm.owner").get("/api/ai/status").json()["suggestions"]
    assert not any("cash" in q.lower() or "margin" in q.lower() for q in ruth)
    assert any("margin" in q for q in owner)


def test_profit_is_withheld_from_roles_without_finance_access(provider, login):
    fake = provider([[("get_sales_trend", {"days": 7})], "ok"])
    ask(login("inv.ruth"), "profit trend?")
    trend = fake.tool_messages()[0]
    assert all("gross_profit" not in d for d in trend["daily"]) and "not part of this user's access" in trend["note"]
    fake = provider([[("get_sales_trend", {"days": 7})], "ok"])
    ask(login("acc.amina"), "profit trend?")
    assert all("gross_profit" in d for d in fake.tool_messages()[0]["daily"])
