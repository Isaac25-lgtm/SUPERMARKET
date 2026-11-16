"""Authentication, sessions, CSRF, lockout, suspension and audit immutability."""
from __future__ import annotations

import psycopg
import pytest
from conftest import TEST_URL, Api, app, q, q1
from fastapi.testclient import TestClient
from motion_api.creds import demo_password


def test_login_and_me(login):
    api = login("acc.amina")
    assert api.me["username"] == "acc.amina"
    assert "accountant" in api.me["roles"]
    assert "admin.users" not in api.me["permissions"]


def test_bad_password_is_rejected_and_audited():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "ops.david", "password": "wrong-password-1"})
    assert r.status_code == 401
    ev = q1("SELECT * FROM motion.audit_event WHERE action='auth.login' AND outcome='denied' ORDER BY id DESC LIMIT 1")
    assert ev and "ops.david" in ev["reason"]


def test_lockout_after_repeated_failures(login):
    owner = login("hm.owner")
    r = owner.post("/api/admin/users", {"username": "lock.test", "full_name": "Lockout Test", "roles": ["cashier"],
                                        "branch_ids": [], "all_branches": True})
    assert r.status_code == 201, r.text
    temp = r.json()["temporary_password"]
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "lock.test", "password": "nope-nope-11"}).status_code == 401
    # Even the correct password is refused while locked.
    r = c.post("/api/auth/login", json={"username": "lock.test", "password": temp})
    assert r.status_code == 401 and "locked" in r.json()["message"].lower()


def test_state_change_requires_csrf(login):
    api = login("cash.cen1")
    r = api.c.post("/api/pos/shifts", json={"register_id": 1, "opening_float": "0"})  # no header
    assert r.status_code == 403
    r = api.c.post("/api/pos/shifts", json={"register_id": 1, "opening_float": "0"}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403


def test_unauthenticated_api_is_rejected():
    assert TestClient(app).get("/api/dashboard").status_code == 401


def test_temporary_password_must_be_changed(login):
    owner = login("hm.owner")
    r = owner.post("/api/admin/users", {"username": "temp.pw", "full_name": "Temp Password", "roles": ["cashier"],
                                        "branch_ids": [], "all_branches": True})
    temp = r.json()["temporary_password"]
    api = Api("temp.pw", temp)
    assert api.me["must_change_password"] is True
    assert api.get("/api/pos/shift").status_code == 403  # blocked until changed
    r = api.post("/api/auth/change-password", {"current_password": temp, "new_password": "NewSecret123x"})
    assert r.status_code == 200, r.text
    assert api.get("/api/pos/shift").status_code == 200


def test_suspension_kills_existing_sessions(login, ids):
    owner = login("hm.owner")
    victim = login("cash.nth2", fresh=True)
    assert victim.get("/api/auth/me").status_code == 200
    r = owner.post(f"/api/admin/users/{ids['user']['cash.nth2']}/suspend", {"reason": "Test suspension"})
    assert r.status_code == 200, r.text
    assert victim.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"username": "cash.nth2", "password": demo_password("cash.nth2")}).status_code == 401
    owner.post(f"/api/admin/users/{ids['user']['cash.nth2']}/reactivate", {"reason": "Test done"})
    ev = q1("SELECT * FROM motion.audit_event WHERE action='admin.user.suspend' ORDER BY id DESC LIMIT 1")
    assert ev["actor_username"] == "hm.owner" and ev["after_state"]["sessions_revoked"] >= 1


def test_audit_trail_is_append_only():
    with psycopg.connect(TEST_URL) as c:
        for stmt in ("UPDATE motion.audit_event SET reason='tampered'", "DELETE FROM motion.audit_event",
                     "TRUNCATE motion.audit_event"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()


def test_approval_history_is_append_only():
    with psycopg.connect(TEST_URL) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM motion.approval_action")


def test_security_headers_and_no_store(login):
    r = login("hm.owner").get("/api/auth/me")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_session_token_never_stored_in_plaintext(login):
    api = login("hm.owner")
    token = api.c.cookies.get("motion_session")
    assert token
    assert q1("SELECT 1 AS x FROM motion.session WHERE id_hash=%s", (token,)) is None
    assert q("SELECT count(*) AS n FROM motion.audit_event WHERE after_state::text LIKE %s", (f"%{token}%",))[0]["n"] == 0
