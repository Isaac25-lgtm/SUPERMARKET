"""Role & branch authorization, enforced server-side (direct API calls)."""
from __future__ import annotations

import pytest

from conftest import ROOT, q1
from motion_api.permissions import markdown


@pytest.mark.parametrize("path", ["/api/admin/users", "/api/audit", "/api/finance/pnl", "/api/finance/ledger",
                                  "/api/approvals/inbox", "/api/insights", "/api/procurement/orders"])
def test_cashier_is_denied_back_office(login, path):
    r = login("cash.cen1").get(path)
    assert r.status_code == 403, (path, r.text)


@pytest.mark.parametrize("username,path", [
    ("bm.sarah", "/api/admin/users"), ("acc.amina", "/api/admin/users"), ("ops.david", "/api/finance/ledger"),
    ("inv.ruth", "/api/finance/pnl"), ("sys.admin", "/api/finance/pnl"), ("sys.admin", "/api/pos/sales"),
    ("gm.grace", "/api/audit"),
])
def test_role_denials(login, username, path):
    assert login(username).get(path).status_code == 403


def test_cross_branch_access_denied(login, ids):
    api = login("cash.nth1")  # North only
    assert api.get("/api/catalog/products", branch_id=ids["branch"]["CEN"]).status_code == 403
    assert api.get(f"/api/pos/registers/{ids['register']['CEN-T1']}/catalog").status_code == 403
    r = api.post("/api/pos/shifts", {"register_id": ids["register"]["CEN-T1"], "opening_float": "0"})
    assert r.status_code == 403
    bm = login("bm.sarah")
    assert bm.get("/api/finance/pnl", branch_id=ids["branch"]["CEN"]).status_code == 403
    assert bm.get("/api/dashboard", branch_id=ids["branch"]["STH"]).status_code == 403
    ev = q1("SELECT * FROM motion.audit_event WHERE actor_username='cash.nth1' AND outcome='denied' ORDER BY id DESC LIMIT 1")
    assert ev is not None


def test_new_cashier_only_sees_assigned_branch(login, ids):
    owner = login("hm.owner")
    r = owner.post("/api/admin/users", {"username": "cash.south9", "full_name": "South Nine (TEST)", "roles": ["cashier"],
                                        "branch_ids": [ids["branch"]["STH"]], "all_branches": False})
    assert r.status_code == 201
    from conftest import Api
    api = Api("cash.south9", r.json()["temporary_password"])
    api.post("/api/auth/change-password", {"current_password": r.json()["temporary_password"], "new_password": "SouthNine123"})
    me = api.get("/api/auth/me").json()
    assert [b["code"] for b in me["branches"]] == ["STH"]
    assert api.get(f"/api/pos/registers/{ids['register']['CEN-T1']}/catalog").status_code == 403
    assert api.get(f"/api/pos/registers/{ids['register']['STH-T1']}/catalog").status_code == 200


def test_cannot_change_own_access_or_status(login, ids):
    owner = login("hm.owner")
    uid = ids["user"]["hm.owner"]
    r = owner.put(f"/api/admin/users/{uid}/access", {"roles": ["owner", "sysadmin"], "branch_ids": [], "all_branches": True,
                                                    "reason": "self escalate"})
    assert r.status_code == 403
    assert owner.post(f"/api/admin/users/{uid}/suspend", {"reason": "self"}).status_code == 403


def test_non_owner_cannot_grant_owner_role(login, ids):
    # Even with a forged request, only an owner may grant the owner role.
    gm = login("gm.grace")
    r = gm.post("/api/admin/users", {"username": "evil.owner", "full_name": "Evil", "roles": ["owner"], "branch_ids": [],
                                     "all_branches": True})
    assert r.status_code == 403


def test_permissions_doc_in_sync():
    doc = (ROOT / "docs" / "role-permissions-matrix.md").read_text(encoding="utf-8")
    assert doc.strip() == markdown().strip(), "regenerate docs/role-permissions-matrix.md"


def test_reduced_access_revokes_sessions(login, ids):
    owner = login("hm.owner")
    target = login("inv.ruth", fresh=True)
    uid = ids["user"]["inv.ruth"]
    r = owner.put(f"/api/admin/users/{uid}/access", {"roles": ["inventory_manager"], "branch_ids": [ids["branch"]["CEN"]],
                                                    "all_branches": False, "reason": "Narrow to Central"})
    assert r.status_code == 200
    assert target.get("/api/auth/me").status_code == 401
    owner.put(f"/api/admin/users/{uid}/access", {"roles": ["inventory_manager"], "branch_ids": list(ids["branch"].values()),
                                                "all_branches": False, "reason": "Restore"})
