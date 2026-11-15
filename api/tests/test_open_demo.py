"""Open-demo (password-less) sign-in: off by default, guarded, still role-limited and audited."""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from conftest import app, q1
from motion_api import security
from motion_api.routes import core
from motion_api.settings import get_settings


def test_open_demo_is_off_by_default():
    c = TestClient(app)
    assert c.get("/api/meta").json()["open_demo"] is False
    assert c.get("/api/auth/demo-accounts").status_code == 404
    assert c.post("/api/auth/demo-login", json={"username": "hm.owner"}).status_code == 404


@pytest.fixture()
def demo_on(monkeypatch):
    on = dataclasses.replace(get_settings(), open_demo=True)
    monkeypatch.setattr(core, "get_settings", lambda: on)
    monkeypatch.setattr(security, "get_settings", lambda: on)
    yield


def test_demo_login_creates_role_limited_audited_session(demo_on, ids):
    c = TestClient(app)
    accounts = {a["username"]: a for a in c.get("/api/auth/demo-accounts").json()["accounts"]}
    assert "cash.nth1" in accounts and "password_hash" not in accounts["cash.nth1"]
    r = c.post("/api/auth/demo-login", json={"username": "cash.nth1"})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    assert c.get("/api/auth/me").json()["roles"] == ["cashier"]
    # Still limited to the role and branch of that account.
    assert c.get("/api/admin/users").status_code == 403
    assert c.get(f"/api/pos/registers/{ids['register']['CEN-T1']}/catalog").status_code == 403
    assert c.post("/api/pos/shifts", json={"register_id": ids["register"]["CEN-T1"], "opening_float": "0"},
                  headers={"X-CSRF-Token": csrf}).status_code == 403
    ev = q1("SELECT * FROM motion.audit_event WHERE action='auth.demo_login' ORDER BY id DESC LIMIT 1")
    assert ev["actor_username"] == "cash.nth1"


def test_demo_login_refuses_unknown_or_pending_accounts(demo_on, login):
    c = TestClient(app)
    assert c.post("/api/auth/demo-login", json={"username": "nobody.here"}).status_code == 401
    owner = login("hm.owner")
    r = owner.post("/api/admin/users", {"username": "pending.demo", "full_name": "Pending Demo", "roles": ["cashier"],
                                        "branch_ids": [], "all_branches": True})
    assert r.status_code == 201
    names = {a["username"] for a in c.get("/api/auth/demo-accounts").json()["accounts"]}
    assert "pending.demo" not in names  # must set a password first
    assert c.post("/api/auth/demo-login", json={"username": "pending.demo"}).status_code == 401


def test_open_demo_cannot_be_enabled_without_test_data_mode(monkeypatch):
    monkeypatch.setenv("MOTION_OPEN_DEMO", "1")
    monkeypatch.setenv("MOTION_DEMO_MODE", "0")
    get_settings.cache_clear()
    try:
        assert get_settings().open_demo is False
    finally:
        get_settings.cache_clear()
