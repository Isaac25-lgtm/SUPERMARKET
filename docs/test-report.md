# Test report

Environment: Windows 11 dev machine; Python 3.12.10; disposable PostgreSQL 18 cluster on 127.0.0.1:55432;
Tryton 8.0.14. Tests drive the real FastAPI app over HTTP (`TestClient`) against the real Tryton engine and
database. No mocks of business logic.

## Backend integration suite
Command:
```bash
MOTION_ENV_FILE=.env.dev PYTHONPATH=api .venv/Scripts/python -m pytest -q
```
Latest result: **63 passed, 0 failed, 0 skipped** (~80 s, plus ~3 min the first time while the fixture template
builds).

| File | Scope |
|---|---|
| `test_auth_security.py` | login, audited failures, lockout, CSRF, temporary passwords, suspension ending sessions, audit/approval immutability (DB), headers, token hashing |
| `test_rbac.py` | role denials (parametrised), cross-branch denials, new cashier branch isolation, no self-escalation, owner-only grant, permission doc in sync, access change revokes sessions |
| `test_pos.py` | exact VAT/COGS ledger effects, idempotency, scale labels, fractional-unit rejection, oversell, **two-thread last-unit race**, override single-use, refund approval and duplicate protection, variance → over/short, unverified mobile money |
| `test_workflows.py` | 100 → 95 transfer conservation + write-off to shrinkage, approval bands & ordering, distinct approvers, full procurement → payment, reject/return/resubmit, accrual expense, default-deny policy |
| `test_finance.py` | trial balance, branch ⇄ consolidated reconciliation, independent SQL check, profit definitions, break-even formula, capital recovery honesty, branch-scoped P&L, audited exports, role-specific dashboards, insight evidence |
| `test_offline.py` | 35 offline sales on 2 tills, restart durability, central price change during outage, exactly-once sync, duplicate resend, stock reconciliation, attribution, edge last-unit race, suspended user losing offline access, outbox immutability, device revocation |

## Defects found by testing and fixed during development
- Failed-login counter was rolled back with the refused login, so lockout never triggered. Fixed: the counter is
  now updated in the separately committed failure transaction.
- Inter-branch dispatch skipped Tryton's `pack` step, so the outgoing leg never completed and the destination was
  credited without stock leaving transit. Fixed, and every workflow transition is now asserted.
- Prices could not be changed through the API (missing company context). Fixed with a default company context.
- Tryton tables were created in a schema named after the DB role. Fixed by pinning `search_path`.
- POS refunds failed (register return location unset). Fixed in base setup.
- Per-sale lock-escalation restarts made checkout take about 1.3 s. Fixed with learned locks; about 0.65 s on the
  dev box.

## Seeded-data integrity check (dev DB, 56 days of TEST history)
Trial balance balanced; 0 unbalanced entries; 0 entries without a branch; branch P&L reconciles for month, last 30
days and FYTD; 0 journal entries from internal transfers; 0 negative stock lines.

## Not yet executed
- Browser end-to-end (Playwright) runs and screenshots: harness installed, runs pending.
- Load test for 10 users / 4 cashiers on Render. Locally, a concurrent two-cashier race is tested. Throughput on
  Render's Starter plan has not been measured.
- Physical-hardware tests (printers, scanners, drawers, UPS).
