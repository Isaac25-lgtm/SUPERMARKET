"""Role → permission matrix: the single source of truth.

``docs/role-permissions-matrix.md`` is generated from this file
(``python -m motion_api.permissions --markdown``) and a test asserts the doc is
in sync.  Authorization is always checked server-side against this map plus
branch scope; the UI only uses it to decide what to show.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    code: str
    name: str
    description: str
    rank: int
    hq: bool  # HQ roles are typically granted all branches; still explicit per user


ROLES: list[Role] = [
    Role("owner", "Head Manager / Owner / CFO",
         "Consolidated finance, final approvals, users & access, audit trail, policy configuration.", 100, True),
    Role("general_manager", "General Manager",
         "Branch performance, escalated procurement approvals, operational exceptions, staff oversight.", 80, True),
    Role("accountant", "Accountant / Finance Manager",
         "Ledger, reconciliations, payables, expense verification, finance checks in approval chains.", 70, True),
    Role("operations_manager", "Operations Manager",
         "Procurement queue, inter-branch transfers, stock balancing, supplier order status.", 70, True),
    Role("inventory_manager", "Inventory / Stock Manager",
         "Stock levels, products & barcodes, reorder requests, receiving, dispatch, counts, adjustments requests.", 60, False),
    Role("branch_manager", "Branch Manager",
         "Local staff & shifts, branch sales, refund and discount approvals within limits, local requests.", 60, False),
    Role("cashier", "Cashier",
         "Point of sale, own shift open/close, own sales history. No purchasing, ledger or admin access.", 10, False),
    Role("sysadmin", "System Administrator (technical)",
         "Technical health, edge-device registration. No business approval authority and no financial data.", 5, False),
]
ROLE_CODES = {r.code for r in ROLES}

# Permission catalogue (code -> description)
PERMISSIONS: dict[str, str] = {
    "dashboard.consolidated": "View consolidated (all authorised branches) dashboard",
    "dashboard.branch": "View branch dashboard for assigned branches",
    "pos.sell": "Operate the POS: sell, take payment, print receipt",
    "pos.shift": "Open / close own cashier shift",
    "pos.history.own": "View own sales and shifts",
    "pos.history.branch": "View all sales and shifts of assigned branches",
    "pos.refund.request": "Request a refund against a prior sale",
    "pos.refund.execute": "Execute an approved refund at the till",
    "pos.override.approve": "Authorise in-person discount/price overrides at the till",
    "pos.shift.reconcile": "Review and reconcile closed shifts and cash variances",
    "catalog.view": "View products, prices and stock levels",
    "catalog.manage": "Create/edit products, barcodes, reorder points",
    "catalog.price": "Change retail prices (audited)",
    "stock.view": "View stock by branch, movements and valuation",
    "stock.receive": "Receive goods against purchase orders",
    "stock.count": "Record stock counts",
    "stock.adjust.request": "Request stock write-off / adjustment",
    "transfer.request": "Request inter-branch transfer",
    "transfer.dispatch": "Dispatch approved transfers from own branch",
    "transfer.receive": "Receive transfers into own branch",
    "procurement.request": "Submit purchase requisitions",
    "procurement.issue_po": "Issue purchase orders for approved requisitions",
    "expense.submit": "Submit expense claims",
    "invoice.post": "Match and post supplier invoices",
    "payment.request": "Request supplier payments",
    "payment.record": "Record approved payments in the ledger",
    "finance.view": "View P&L, cash, payables, break-even, capital recovery",
    "finance.ledger": "Inspect journal entries and source documents",
    "finance.investment": "Record investment amounts used for capital recovery",
    "reports.export": "Export reports (CSV) within branch scope (audited)",
    "approvals.inbox": "See and act on approval requests addressed to own roles",
    "admin.users": "Create/suspend users, grant roles and branches, revoke sessions",
    "admin.policies": "Change approval thresholds and steps",
    "audit.view": "Search the audit trail",
    "audit.export": "Export audit trail (CSV)",
    "insights.view": "Use Ask Motion AI and view its alerts",
    "sync.devices": "Register/revoke branch edge devices",
    "system.health": "View technical health",
}

_ALL_FINANCE = {"finance.view", "finance.ledger", "reports.export"}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {
        "dashboard.consolidated", "dashboard.branch", "pos.history.branch", "pos.shift.reconcile",
        "catalog.view", "stock.view", "approvals.inbox", "admin.users", "admin.policies",
        "audit.view", "audit.export", "insights.view", "finance.investment", "pos.override.approve",
        *_ALL_FINANCE,
    },
    "general_manager": {
        "dashboard.consolidated", "dashboard.branch", "pos.history.branch", "catalog.view",
        "stock.view", "approvals.inbox", "insights.view", "finance.view", "reports.export",
        "pos.override.approve", "procurement.request",
    },
    "accountant": {
        "dashboard.consolidated", "dashboard.branch", "pos.history.branch", "pos.shift.reconcile",
        "catalog.view", "stock.view", "approvals.inbox", "expense.submit", "invoice.post",
        "payment.request", "payment.record", "finance.investment", "insights.view", *_ALL_FINANCE,
    },
    "operations_manager": {
        "dashboard.consolidated", "dashboard.branch", "catalog.view", "stock.view",
        "approvals.inbox", "procurement.request", "procurement.issue_po", "transfer.request",
        "insights.view", "reports.export",
    },
    "inventory_manager": {
        "dashboard.branch", "catalog.view", "catalog.manage", "catalog.price", "stock.view",
        "stock.receive", "stock.count", "stock.adjust.request", "transfer.request",
        "transfer.dispatch", "transfer.receive", "procurement.request", "approvals.inbox",
        "insights.view",
    },
    "branch_manager": {
        "dashboard.branch", "pos.history.branch", "pos.shift.reconcile", "pos.override.approve",
        "pos.refund.request", "catalog.view", "stock.view", "stock.count", "stock.adjust.request",
        "transfer.request", "transfer.dispatch", "transfer.receive", "procurement.request",
        "expense.submit", "approvals.inbox", "finance.view", "reports.export", "insights.view",
    },
    "cashier": {
        "pos.sell", "pos.shift", "pos.history.own", "pos.refund.request", "pos.refund.execute",
        "catalog.view",
    },
    "sysadmin": {"system.health", "sync.devices"},
}

# finance.view for branch managers is limited to their own branches by branch scope.

# Which role may act on which approval step kind is defined by policy steps
# (motion.approval_policy.steps); roles below are the only ones allowed to be
# named as approvers per action type (defence against a policy typo granting
# a cashier approval power).
APPROVER_ROLES: dict[str, set[str]] = {
    "purchase_request": {"operations_manager", "accountant", "general_manager", "owner"},
    "expense": {"accountant", "general_manager", "owner", "branch_manager"},
    "supplier_payment": {"accountant", "general_manager", "owner"},
    "transfer": {"operations_manager", "general_manager", "owner"},
    "refund": {"branch_manager", "general_manager", "owner"},
    "stock_writeoff": {"branch_manager", "operations_manager", "accountant", "general_manager", "owner"},
    "price_override": {"branch_manager", "general_manager", "owner"},
}
ACTION_TYPES = set(APPROVER_ROLES)

for _r, _perms in ROLE_PERMISSIONS.items():
    _unknown = _perms - PERMISSIONS.keys()
    assert not _unknown, f"unknown permissions for {_r}: {_unknown}"
assert set(ROLE_PERMISSIONS) == ROLE_CODES


def permissions_for(roles: set[str] | list[str]) -> set[str]:
    out: set[str] = set()
    for r in roles:
        out |= ROLE_PERMISSIONS.get(r, set())
    return out


# Areas for the "My access" view: (label, permission-code prefixes), in display order.
AREAS: list[tuple[str, tuple[str, ...]]] = [
    ("Dashboards", ("dashboard.",)),
    ("Point of sale & shifts", ("pos.",)),
    ("Products & prices", ("catalog.",)),
    ("Stock & transfers", ("stock.", "transfer.")),
    ("Procurement", ("procurement.",)),
    ("Payables & expenses", ("expense.", "invoice.", "payment.")),
    ("Finance & reports", ("finance.", "reports.")),
    ("Approvals", ("approvals.",)),
    ("Ask Motion AI & alerts", ("insights.",)),
    ("Administration & audit", ("admin.", "audit.")),
    ("Technical", ("sync.", "system.")),
]

ACTION_LABELS: dict[str, str] = {
    "purchase_request": "Purchase requisitions",
    "expense": "Expense claims",
    "supplier_payment": "Supplier payments",
    "transfer": "Inter-branch transfers",
    "refund": "Refunds",
    "stock_writeoff": "Stock write-offs",
    "price_override": "Till discounts and price overrides",
}
assert set(ACTION_LABELS) == ACTION_TYPES


def access_summary(roles: set[str] | list[str]) -> dict:
    """Human-readable roles, permissions by area and approval authority for one account."""
    roles = set(roles)
    granted = permissions_for(roles)
    areas = []
    for label, prefixes in AREAS:
        codes = [c for c in PERMISSIONS if c.startswith(prefixes)]
        areas.append({"area": label, "granted": sum(c in granted for c in codes), "total": len(codes),
                      "permissions": [{"code": c, "label": PERMISSIONS[c], "granted": c in granted} for c in codes]})
    assert sum(a["total"] for a in areas) == len(PERMISSIONS), "every permission belongs to an area"
    return {
        "roles": [{"code": r.code, "name": r.name, "description": r.description} for r in ROLES if r.code in roles],
        "areas": areas,
        "granted": len(granted), "total": len(PERMISSIONS),
        "can_approve": [ACTION_LABELS[a] for a in ACTION_LABELS if APPROVER_ROLES[a] & roles],
    }


def roles_directory() -> dict:
    """Every role: what it does, which approvals it may sign and its permissions by area.

    Drives the "All roles" view so everyone can see who is responsible for what. It is the
    same matrix the server enforces; nothing here is specific to a person.
    """
    roles = []
    for r in ROLES:
        perms = ROLE_PERMISSIONS[r.code]
        areas = []
        for label, prefixes in AREAS:
            codes = [c for c in PERMISSIONS if c.startswith(prefixes) and c in perms]
            if codes:
                areas.append({"area": label, "permissions": [{"code": c, "label": PERMISSIONS[c]} for c in codes]})
        roles.append({"code": r.code, "name": r.name, "description": r.description, "head_office": r.hq,
                      "granted": len(perms), "areas": areas,
                      "can_approve": [ACTION_LABELS[a] for a in ACTION_LABELS if r.code in APPROVER_ROLES[a]]})
    matrix = [{"area": label, "permissions": [
        {"code": c, "label": PERMISSIONS[c], "roles": [r.code for r in ROLES if c in ROLE_PERMISSIONS[r.code]]}
        for c in PERMISSIONS if c.startswith(prefixes)]} for label, prefixes in AREAS]
    approvals = [{"action": ACTION_LABELS[a], "roles": [r.code for r in ROLES if r.code in APPROVER_ROLES[a]]}
                 for a in ACTION_LABELS]
    return {"roles": roles, "matrix": matrix, "approvals": approvals, "total": len(PERMISSIONS)}


def markdown() -> str:
    roles = [r.code for r in ROLES]
    head = "| Permission | " + " | ".join(roles) + " |"
    sep = "|---|" + "|".join(":-:" for _ in roles) + "|"
    rows = []
    for code, desc in PERMISSIONS.items():
        cells = ["✔" if code in ROLE_PERMISSIONS[r] else "" for r in roles]
        rows.append(f"| `{code}` — {desc} | " + " | ".join(cells) + " |")
    approver = ["", "## Roles that may be named as approvers, per action type", "",
                "| Action type | Allowed approver roles |", "|---|---|"]
    for act, rs in APPROVER_ROLES.items():
        approver.append(f"| `{act}` | {', '.join(sorted(rs))} |")
    role_tbl = ["## Roles", "", "| Code | Name | Responsibility |", "|---|---|---|"]
    role_tbl += [f"| `{r.code}` | {r.name} | {r.description} |" for r in ROLES]
    return "\n".join([
        "# Role & permission matrix",
        "",
        "_Generated from `api/motion_api/permissions.py` — do not edit by hand. "
        "Regenerate with `python -m motion_api.permissions --markdown > docs/role-permissions-matrix.md`._",
        "",
        "Every permission is enforced server-side **and** combined with branch scope: a user only "
        "sees or acts on branches explicitly granted to them (HQ users can be granted all branches).",
        "",
        *role_tbl,
        "",
        "## Permissions",
        "",
        head, sep, *rows, *approver, "",
        "## Hard rules enforced in code (independent of this matrix)",
        "",
        "- No self-approval: the initiator of a request can never act on any of its approval steps.",
        "- One person cannot approve two steps of the same request (distinct approvers per step).",
        "- Steps are strictly ordered; acting on a later step before earlier steps are complete is rejected.",
        "- Amount bands select the policy; an approver must hold the step's role (or an unexpired, "
        "audited delegation of it) **and** branch scope over the request's branch.",
        "- Users cannot grant roles to themselves, change their own status or branch scope.",
        "- Audit events are append-only (database trigger rejects UPDATE/DELETE/TRUNCATE).",
        "",
    ])


if __name__ == "__main__":
    if "--markdown" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8")
        print(markdown())
