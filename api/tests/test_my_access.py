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


def test_roles_directory_matches_matrix_and_hides_people_from_non_admins(login):
    from motion_api.permissions import APPROVER_ROLES, ROLES

    cashier = login("cash.nth1").get("/api/auth/roles")
    assert cashier.status_code == 200, cashier.text
    body = cashier.json()
    assert [r["code"] for r in body["roles"]] == [r.code for r in ROLES]
    assert body["my_roles"] == ["cashier"] and body["shows_people"] is False
    assert all("people" not in r for r in body["roles"])
    for r in body["roles"]:
        codes = {p["code"] for a in r["areas"] for p in a["permissions"]}
        assert codes == ROLE_PERMISSIONS[r["code"]] and r["granted"] == len(codes)
    # the comparison matrix lists every permission once, with exactly the roles that hold it
    rows = {p["code"]: set(p["roles"]) for a in body["matrix"] for p in a["permissions"]}
    assert set(rows) == set(PERMISSIONS)
    assert all(rows[c] == {r for r, ps in ROLE_PERMISSIONS.items() if c in ps} for c in rows)
    assert len(body["approvals"]) == len(APPROVER_ROLES)
    bm = next(r for r in body["roles"] if r["code"] == "branch_manager")
    assert "Refunds" in bm["can_approve"] and "Supplier payments" not in bm["can_approve"]

    # a branch manager (not a company-wide admin) also gets no names
    assert login("bm.sarah").get("/api/auth/roles").json()["shows_people"] is False

    owner = login("hm.owner").get("/api/auth/roles").json()
    assert owner["shows_people"] is True
    cashiers = next(r for r in owner["roles"] if r["code"] == "cashier")["people"]
    assert "cash.nth1" in {u["username"] for u in cashiers}


def test_roles_directory_requires_sign_in():
    from fastapi.testclient import TestClient
    from motion_api.main import app

    assert TestClient(app).get("/api/auth/roles").status_code == 401
