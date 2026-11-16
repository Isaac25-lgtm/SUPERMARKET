"""The "My access" panel: each account sees exactly its own roles, branches and permissions."""
from __future__ import annotations

from motion_api.permissions import PERMISSIONS, ROLE_PERMISSIONS


def _granted(body: dict) -> set[str]:
    return {p["code"] for a in body["areas"] for p in a["permissions"] if p["granted"]}


def test_cashier_access_summary_matches_role(login):
    r = login("cash.nth1").get("/api/auth/access")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["username"] == "cash.nth1"
    assert [x["code"] for x in body["roles"]] == ["cashier"]
    assert _granted(body) == ROLE_PERMISSIONS["cashier"]
    assert body["granted"] == len(ROLE_PERMISSIONS["cashier"]) and body["total"] == len(PERMISSIONS)
    assert body["can_approve"] == []
    assert not body["all_branches"] and [b["code"] for b in body["branches"]] == ["NTH"]
    # every permission is listed once, with a human-readable label
    codes = [p["code"] for a in body["areas"] for p in a["permissions"]]
    assert sorted(codes) == sorted(PERMISSIONS) and all(p["label"] for a in body["areas"] for p in a["permissions"])


def test_head_office_and_manager_summaries(login):
    owner = login("hm.owner").get("/api/auth/access").json()
    assert owner["all_branches"] and "owner" in {x["code"] for x in owner["roles"]}
    assert "Refunds" in owner["can_approve"] and "Supplier payments" in owner["can_approve"]
    bm = login("bm.sarah").get("/api/auth/access").json()
    assert "Supplier payments" not in bm["can_approve"] and "Refunds" in bm["can_approve"]
    assert "admin.users" not in _granted(bm)


def test_access_summary_requires_sign_in():
    from fastapi.testclient import TestClient
    from motion_api.main import app

    assert TestClient(app).get("/api/auth/access").status_code == 401
