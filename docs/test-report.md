# Test report

Environment: Windows 11 dev machine; Python 3.12.10; disposable PostgreSQL 18 cluster on 127.0.0.1:55432;
Tryton 8.0.14. Tests drive the real FastAPI app over HTTP (`TestClient`) against the real Tryton engine and
database. No mocks of business logic.

## Backend integration suite
Command:
```bash
MOTION_ENV_FILE=.env.dev PYTHONPATH=api .venv/Scripts/python -m pytest -q
```
Latest result: **106 passed, 0 failed, 0 skipped** (~90 s, plus ~3 min the first time while the fixture template
builds).

| File | Scope |
|---|---|
| `test_auth_security.py` | login, audited failures, lockout, CSRF, temporary passwords, suspension ending sessions, audit/approval immutability (DB), headers, token hashing |
| `test_rbac.py` | role denials (parametrised), cross-branch denials, new cashier branch isolation, no self-escalation, owner-only grant, permission doc in sync, access change revokes sessions |
| `test_pos.py` | exact VAT/COGS ledger effects, idempotency, scale labels, fractional-unit rejection, oversell, **two-thread last-unit race**, override single-use, refund approval and duplicate protection, variance → over/short, unverified mobile money |
| `test_workflows.py` | 100 → 95 transfer conservation + write-off to shrinkage, approval bands & ordering, distinct approvers, full procurement → payment, reject/return/resubmit, accrual expense, default-deny policy |
| `test_finance.py` | trial balance, branch ⇄ consolidated reconciliation, independent SQL check, profit definitions, break-even formula, capital recovery honesty, branch-scoped P&L, audited exports, role-specific dashboards, insight evidence |
| `test_open_demo.py` | open demo off by default (404), role-limited audited demo sessions, pending accounts excluded, cannot enable without TEST DATA mode |
| `test_ai.py` | Ask Motion AI with a mocked provider: tool loop + logging + audit, branch-scope and permission denials inside tools, unknown tools/bad arguments contained, data minimisation (no per-cashier/cost data sent), provider failure → rule-based fallback, hourly limit, cashier denied, bounded history, role-filtered tools, role-aware suggestions, section links limited to pages the user can open |
| `test_stage1_hardening.py` | Codex stage 1: demo isolation (C2), client deployment refuses demo switches, idle sign-out, approval/branch-scope hardening (C1, H1) |
| `test_barcodes.py` | GS1 check digits, UPC-A/EAN-13 equivalence, multiple barcodes per product, duplicate rejection, audited add/remove, unknown-barcode message |
| `test_stage2_money.py` | Codex stage 2: idempotent sale/refund/deposit (replay, changed payload, per-cashier scope, two-thread duplicate), half-even weighted lines, partial refunds closing gross and VAT exactly, one open refund per receipt, duplicate lines, cash-only till refunds, original-VAT reversal, close-vs-checkout serialization |
| `test_offline.py` | 35 offline sales on 2 tills, restart durability, central price change during outage, exactly-once sync, duplicate resend, stock reconciliation, attribution, edge last-unit race, suspended user losing offline access, outbox immutability, device revocation |

## Defects found by testing and fixed during development
- Failed-login counter was rolled back with the refused login, so lockout never triggered. Fixed: the counter is
  now updated in the separately committed failure transaction.
- Inter-branch dispatch skipped Tryton's `pack` step, so the outgoing leg never completed and the destination was
  credited without stock leaving transit. Fixed, and every workflow transition is now asserted.
- Prices could not be changed through the API (missing company context). Fixed with a default company context.
- Tryton tables were created in a schema named after the DB role. Fixed by pinning `search_path`.
- POS refunds failed (register return location unset). Fixed in base setup.
- TEST DATA showed an overdrawn bank (takings never banked). Added the bank-deposit operation and weekly seeded deposits; expenses rescaled to the simulated sales volume.
- Per-sale lock-escalation restarts made checkout take about 1.3 s. Fixed with learned locks; about 0.65 s on the
  dev box.

## Seeded-data integrity check (dev DB, 56 days of TEST history)
Trial balance balanced; 0 unbalanced entries; 0 entries without a branch; branch P&L reconciles for month, last 30
days and FYTD; 0 journal entries from internal transfers; 0 negative stock lines.

## Browser E2E (Playwright, Chromium, local server on seeded dev DB)
Command: `cd web && E2E_CREDS=<creds.json> npx playwright test -c e2e/playwright.config.ts`. Result: **11 passed**:
- the six role dashboards render live data with no page errors (screenshots taken at desktop size, plus mobile
  for the Head Manager and Branch Manager);
- every navigation route renders for the Head Manager;
- a cashier completes a sale and gets a numbered receipt;
- a cashier is blocked from back-office pages.
- the company logo returns to the dashboard, and sign-out lands on the sign-in page;
- Ask Motion AI: the floating button opens the chat on any page, a question gets an answer, Escape closes it, and the Alerts & insights page renders.

Screenshot review found grey charts (chart colour tokens weren't emitted by Tailwind). Fixed with `@theme static`.

## Not yet executed
- Load test for 10 users / 4 cashiers on Render. Locally, a concurrent two-cashier race is tested. Throughput on
  Render's Starter plan has not been measured.
- Physical-hardware tests (printers, scanners, drawers, UPS).
