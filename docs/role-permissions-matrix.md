# Role & permission matrix

_Generated from `api/motion_api/permissions.py` — do not edit by hand. Regenerate with `python -m motion_api.permissions --markdown > docs/role-permissions-matrix.md`._

Every permission is enforced server-side **and** combined with branch scope: a user only sees or acts on branches explicitly granted to them (HQ users can be granted all branches).

## Roles

| Code | Name | Responsibility |
|---|---|---|
| `owner` | Head Manager / Owner / CFO | Consolidated finance, final approvals, users & access, audit trail, policy configuration. |
| `general_manager` | General Manager | Branch performance, escalated procurement approvals, operational exceptions, staff oversight. |
| `accountant` | Accountant / Finance Manager | Ledger, reconciliations, payables, expense verification, finance checks in approval chains. |
| `operations_manager` | Operations Manager | Procurement queue, inter-branch transfers, stock balancing, supplier order status. |
| `inventory_manager` | Inventory / Stock Manager | Stock levels, products & barcodes, reorder requests, receiving, dispatch, counts, adjustments requests. |
| `branch_manager` | Branch Manager | Local staff & shifts, branch sales, refund and discount approvals within limits, local requests. |
| `cashier` | Cashier | Point of sale, own shift open/close, own sales history. No purchasing, ledger or admin access. |
| `sysadmin` | System Administrator (technical) | Technical health, edge-device registration. No business approval authority and no financial data. |

## Permissions

| Permission | owner | general_manager | accountant | operations_manager | inventory_manager | branch_manager | cashier | sysadmin |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `dashboard.consolidated` — View consolidated (all authorised branches) dashboard | ✔ | ✔ | ✔ | ✔ |  |  |  |  |
| `dashboard.branch` — View branch dashboard for assigned branches | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |  |
| `pos.sell` — Operate the POS: sell, take payment, print receipt |  |  |  |  |  |  | ✔ |  |
| `pos.shift` — Open / close own cashier shift |  |  |  |  |  |  | ✔ |  |
| `pos.history.own` — View own sales and shifts |  |  |  |  |  |  | ✔ |  |
| `pos.history.branch` — View all sales and shifts of assigned branches | ✔ | ✔ | ✔ |  |  | ✔ |  |  |
| `pos.refund.request` — Request a refund against a prior sale |  |  |  |  |  | ✔ | ✔ |  |
| `pos.refund.execute` — Execute an approved refund at the till |  |  |  |  |  |  | ✔ |  |
| `pos.override.approve` — Authorise in-person discount/price overrides at the till | ✔ | ✔ |  |  |  | ✔ |  |  |
| `pos.shift.reconcile` — Review and reconcile closed shifts and cash variances | ✔ |  | ✔ |  |  | ✔ |  |  |
| `catalog.view` — View products, prices and stock levels | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |
| `catalog.manage` — Create/edit products, barcodes, reorder points |  |  |  |  | ✔ |  |  |  |
| `catalog.price` — Change retail prices (audited) |  |  |  |  | ✔ |  |  |  |
| `stock.view` — View stock by branch, movements and valuation | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |  |
| `stock.receive` — Receive goods against purchase orders |  |  |  |  | ✔ |  |  |  |
| `stock.count` — Record stock counts |  |  |  |  | ✔ | ✔ |  |  |
| `stock.adjust.request` — Request stock write-off / adjustment |  |  |  |  | ✔ | ✔ |  |  |
| `transfer.request` — Request inter-branch transfer |  |  |  | ✔ | ✔ | ✔ |  |  |
| `transfer.dispatch` — Dispatch approved transfers from own branch |  |  |  |  | ✔ | ✔ |  |  |
| `transfer.receive` — Receive transfers into own branch |  |  |  |  | ✔ | ✔ |  |  |
| `procurement.request` — Submit purchase requisitions |  | ✔ |  | ✔ | ✔ | ✔ |  |  |
| `procurement.issue_po` — Issue purchase orders for approved requisitions |  |  |  | ✔ |  |  |  |  |
| `expense.submit` — Submit expense claims |  |  | ✔ |  |  | ✔ |  |  |
| `invoice.post` — Match and post supplier invoices |  |  | ✔ |  |  |  |  |  |
| `payment.request` — Request supplier payments |  |  | ✔ |  |  |  |  |  |
| `payment.record` — Record approved payments in the ledger |  |  | ✔ |  |  |  |  |  |
| `finance.view` — View P&L, cash, payables, break-even, capital recovery | ✔ | ✔ | ✔ |  |  | ✔ |  |  |
| `finance.ledger` — Inspect journal entries and source documents | ✔ |  | ✔ |  |  |  |  |  |
| `finance.investment` — Record investment amounts used for capital recovery | ✔ |  | ✔ |  |  |  |  |  |
| `reports.export` — Export reports (CSV) within branch scope (audited) | ✔ | ✔ | ✔ | ✔ |  | ✔ |  |  |
| `approvals.inbox` — See and act on approval requests addressed to own roles | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |  |
| `admin.users` — Create/suspend users, grant roles and branches, revoke sessions | ✔ |  |  |  |  |  |  |  |
| `admin.policies` — Change approval thresholds and steps | ✔ |  |  |  |  |  |  |  |
| `audit.view` — Search the audit trail | ✔ |  |  |  |  |  |  |  |
| `audit.export` — Export audit trail (CSV) | ✔ |  |  |  |  |  |  |  |
| `insights.view` — Use Ask Motion AI and view its alerts | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |  |  |
| `sync.devices` — Register/revoke branch edge devices |  |  |  |  |  |  |  | ✔ |
| `system.health` — View technical health |  |  |  |  |  |  |  | ✔ |

## Roles that may be named as approvers, per action type

| Action type | Allowed approver roles |
|---|---|
| `purchase_request` | accountant, general_manager, operations_manager, owner |
| `expense` | accountant, branch_manager, general_manager, owner |
| `supplier_payment` | accountant, general_manager, owner |
| `transfer` | general_manager, operations_manager, owner |
| `refund` | branch_manager, general_manager, owner |
| `stock_writeoff` | accountant, branch_manager, general_manager, operations_manager, owner |
| `price_override` | branch_manager, general_manager, owner |

## Hard rules enforced in code (independent of this matrix)

- No self-approval: the initiator of a request can never act on any of its approval steps.
- One person cannot approve two steps of the same request (distinct approvers per step).
- Steps are strictly ordered; acting on a later step before earlier steps are complete is rejected.
- Amount bands select the policy; an approver must hold the step's role (or an unexpired, audited delegation of it) **and** branch scope over the request's branch.
- Users cannot grant roles to themselves, change their own status or branch scope.
- Audit events are append-only (database trigger rejects UPDATE/DELETE/TRUNCATE).
