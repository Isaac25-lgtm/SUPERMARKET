"""Regression tests for the Codex audit, stage 1 (C1, C2, H1) and the idle sign-out requirement."""
from __future__ import annotations

import dataclasses

import psycopg
import pytest
from conftest import TEST_URL, Api, app, new_key, on_hand, pid, q1
from fastapi.testclient import TestClient
from motion_api import security
from motion_api.routes import core
from motion_api.settings import get_settings

# ---------------------------------------------------------------- C2: demo isolation

@pytest.fixture()
def demo_on(monkeypatch):
    on = dataclasses.replace(get_settings(), open_demo=True)
    monkeypatch.setattr(core, "get_settings", lambda: on)
    monkeypatch.setattr(security, "get_settings", lambda: on)
    return monkeypatch


def test_only_marked_demo_accounts_can_use_the_picker(demo_on, login):
    owner = login("hm.owner")
    r = owner.post("/api/admin/users", {"username": "real.staff", "full_name": "Real Staff", "roles": ["accountant"],
                                        "branch_ids": [], "all_branches": True})
    temp = r.json()["temporary_password"]
    staff = Api("real.staff", temp)
    staff.post("/api/auth/change-password", {"current_password": temp, "new_password": "RealStaff12345"})
    c = TestClient(app)
    names = {a["username"] for a in c.get("/api/auth/demo-accounts").json()["accounts"]}
    assert "real.staff" not in names and "hm.owner" in names
    assert c.post("/api/auth/demo-login", json={"username": "real.staff"}).status_code == 401


def test_demo_sessions_die_when_demo_is_switched_off(demo_on):
    c = TestClient(app)
    assert c.post("/api/auth/demo-login", json={"username": "acc.amina"}).status_code == 200
    assert c.get("/api/auth/me").status_code == 200
    off = dataclasses.replace(get_settings(), open_demo=False)
    demo_on.setattr(security, "get_settings", lambda: off)
    assert c.get("/api/auth/me").status_code == 401
    session = q1("SELECT revoked_reason FROM motion.session WHERE origin='demo' ORDER BY created_at DESC LIMIT 1")
    assert session["revoked_reason"] == "demo_closed"


@pytest.mark.parametrize("flag", ["MOTION_OPEN_DEMO", "MOTION_SEED_DEMO", "MOTION_DEMO_MODE"])
def test_client_deployment_refuses_demo_switches(monkeypatch, flag):
    monkeypatch.setenv("MOTION_DEPLOYMENT", "client")
    for f in ("MOTION_OPEN_DEMO", "MOTION_SEED_DEMO", "MOTION_DEMO_MODE"):
        monkeypatch.setenv(f, "0")
    monkeypatch.setenv(flag, "1")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="client deployment"):
            get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        get_settings()


def test_client_deployment_defaults_everything_off(monkeypatch):
    monkeypatch.setenv("MOTION_DEPLOYMENT", "client")
    for f in ("MOTION_OPEN_DEMO", "MOTION_SEED_DEMO", "MOTION_DEMO_MODE"):
        monkeypatch.delenv(f, raising=False)
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert (s.open_demo, s.seed_demo, s.demo_mode) == (False, False, False)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        get_settings()


# ---------------------------------------------------------------- idle sign-out (5 minutes)

def test_session_expires_after_five_idle_minutes(login):
    api = login("gm.grace", fresh=True)
    assert api.get("/api/auth/me").status_code == 200
    token = api.c.cookies.get("motion_session")
    with psycopg.connect(TEST_URL) as c:
        c.execute("UPDATE motion.session SET last_seen_at = now() - interval '6 minutes' WHERE id_hash = encode(sha256(%s::bytea), 'hex')",
                  (token.encode(),))
        c.commit()
    r = api.get("/api/auth/me")
    assert r.status_code == 401 and "inactivity" in r.json()["message"]
    assert TestClient(app).get("/api/meta").json()["session_idle_minutes"] == 5


# ---------------------------------------------------------------- C1: approvals keep domain validation

def test_returned_request_cannot_be_edited_generically(login, ids):
    sup = q1("SELECT id FROM party_party WHERE name LIKE 'Pearl%%'")["id"]
    r = login("inv.ruth").post("/api/procurement/requisitions", {
        "branch_id": ids["branch"]["NTH"], "supplier_id": sup, "justification": "Correction test",
        "lines": [{"product_id": pid(ids, "Bleach"), "quantity": 10}]}).json()
    assert login("ops.david").post(f"/api/approvals/{r['id']}/decision", {"decision": "return", "comment": "Fix qty"}).status_code == 200
    forged = dict(r["payload"], supplier_id=1)
    rs = login("inv.ruth").post(f"/api/approvals/{r['id']}/resubmit", {"payload": forged, "amount": "1"})
    assert rs.status_code == 409
    assert q1("SELECT status, amount FROM motion.approval_request WHERE id=%s", (r["id"],))["status"] == "returned"


def test_writeoff_rechecks_stock_at_execution(login, ids, open_shift):
    product = pid(ids, "Candles")
    loc = ids["storage"]["STH"]
    have = int(on_hand(loc, product))
    moses = login("bm.moses")
    wo = moses.post("/api/stock/writeoffs", {"branch_id": ids["branch"]["STH"], "product_id": product, "quantity": have,
                                             "reason": "Water damage in store room", "kind": "damage"}).json()
    # Meanwhile the till sells one unit, so the approved quantity is no longer on the shelf.
    api, reg, _ = open_shift("cash.sth1", "STH-T1")
    assert api.post("/api/pos/sales", {"register_id": reg, "idempotency_key": new_key(),
                                       "lines": [{"product_id": product, "quantity": 1}],
                                       "payments": [{"method": "cash", "amount": "5000"}]}).status_code == 201
    login("ops.david").post(f"/api/approvals/{wo['id']}/decision", {"decision": "approve", "comment": "ok"})
    r = moses.post(f"/api/stock/writeoffs/{wo['id']}/execute")
    assert r.status_code == 409 and "on hand" in r.json()["message"]
    assert on_hand(loc, product) == have - 1  # nothing written off, never negative


# ---------------------------------------------------------------- H1: branch scope

@pytest.fixture()
def restricted_owner(login, ids):
    owner = login("hm.owner")
    r = owner.post("/api/admin/users", {"username": "owner.north", "full_name": "North-only Owner", "roles": ["owner"],
                                        "branch_ids": [ids["branch"]["NTH"]], "all_branches": False})
    if r.status_code == 409:
        return login("owner.north")
    temp = r.json()["temporary_password"]
    api = Api("owner.north", temp)
    api.post("/api/auth/change-password", {"current_password": temp, "new_password": "NorthOwner12345"})
    return Api("owner.north", "NorthOwner12345")


def test_company_wide_reports_and_admin_need_all_branches(restricted_owner):
    api = restricted_owner
    assert api.get("/api/finance/trial-balance").status_code == 403
    assert api.get("/api/finance/capital-recovery").status_code == 403
    assert api.post("/api/finance/investments", {"label": "Branch fit-out", "amount": "1", "invested_on": "2026-01-01"}).status_code == 403
    assert api.get("/api/admin/users").status_code == 403
    assert api.get("/api/admin/delegations").status_code == 403
    assert api.get("/api/dashboard").status_code == 200  # dashboard degrades gracefully
    assert api.get("/api/finance/pnl", period="month").status_code == 200  # branch P&L still allowed


def test_payment_request_checks_branch_before_revealing_state(login, ids):
    # A Central bill exists from posting a Central expense.
    e = login("bm.joseph").post("/api/finance/expenses", {
        "branch_id": ids["branch"]["CEN"], "category": "repairs", "payee": "Fix-It (TEST)", "amount": "40000",
        "description": "Freezer repair", "incurred_on": "2026-11-12"}).json()
    for u in ("acc.amina", "gm.grace"):
        login(u).post(f"/api/approvals/{e['id']}/decision", {"decision": "approve", "comment": "ok"})
    inv = login("acc.amina").post(f"/api/finance/expenses/{e['id']}/post").json()
    owner = login("hm.owner")
    r = owner.post("/api/admin/users", {"username": "acc.north", "full_name": "North Accountant", "roles": ["accountant"],
                                        "branch_ids": [ids["branch"]["NTH"]], "all_branches": False})
    temp = r.json()["temporary_password"]
    a = Api("acc.north", temp)
    a.post("/api/auth/change-password", {"current_password": temp, "new_password": "NorthAcc12345"})
    a = Api("acc.north", "NorthAcc12345")
    assert a.get(f"/api/finance/invoices/{inv['id']}").status_code == 403
    resp = a.post(f"/api/finance/invoices/{inv['id']}/payment-request", {"amount": "40000", "reason": "pay"})
    assert resp.status_code == 403 and "posted" not in resp.json()["message"].lower()


def test_initiator_loses_visibility_with_branch_scope(login, ids):
    sarah = login("bm.sarah")
    tr = sarah.post("/api/transfers", {"from_branch_id": ids["branch"]["CEN"], "to_branch_id": ids["branch"]["NTH"],
                                       "lines": [{"product_id": pid(ids, "Soda 500ml"), "quantity": 2}], "reason": "scope test"}).json()
    approval_id = tr["approval_id"]
    assert sarah.get(f"/api/approvals/{approval_id}").status_code == 200
    owner = login("hm.owner")
    uid = ids["user"]["bm.sarah"]
    owner.put(f"/api/admin/users/{uid}/access", {"roles": ["branch_manager"], "branch_ids": [ids["branch"]["STH"]],
                                                "all_branches": False, "reason": "moved to South"})
    moved = login("bm.sarah", fresh=True)
    assert moved.get(f"/api/approvals/{approval_id}").status_code == 403
    owner.put(f"/api/admin/users/{uid}/access", {"roles": ["branch_manager"], "branch_ids": [ids["branch"]["NTH"]],
                                                "all_branches": False, "reason": "restore"})
