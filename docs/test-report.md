# Test report

Environment: Windows 11 dev machine; Python 3.12.10; disposable PostgreSQL 18 cluster on 127.0.0.1:55432;
Tryton 8.0.14. Tests drive the real FastAPI app over HTTP (`TestClient`) against the real Tryton engine and
database. No mocks of business logic.

## Backend integration suite
Command:
```bash
MOTION_ENV_FILE=.env.dev PYTHONPATH=api .venv/Scripts/python -m pytest -q
```
Latest result (16 Nov 2026, after the second review): **116 passed, 0 failed, 0 skipped** (~145 s, plus ~3 min the first time while the fixture template
builds).

| File | Scope |
|---|---|
| `test_auth_security.py` | login, audited failures, lockout, CSRF, temporary passwords, suspension ending sessions, audit/approval immutability (DB), headers, token hashing |
| `test_rbac.py` | role denials (parametrised), cross-branch denials, new cashier branch isolation, no self-escalation, owner-only grant, permission doc in sync, access change revokes sessions |
| `test_pos.py` | exact VAT/COGS ledger effects, idempotency, scale labels, fractional-unit rejection, oversell, **two-thread last-unit race**, override single-use, refund approval and duplicate protection, variance → over/short, unverified mobile money |
| `test_workflows.py` | 100 → 95 transfer conservation + write-off to shrinkage, approval bands & ordering, distinct approvers, full procurement → payment (wrong supplier total refused), bill VAT must match the PO, payables summary totals, reject/return/resubmit, accrual expense, default-deny policy |
| `test_finance.py` | trial balance, branch ⇄ consolidated reconciliation, independent SQL check, profit definitions, break-even formula, capital recovery honesty, branch-scoped P&L, audited exports, role-specific dashboards, insight evidence |
| `test_open_demo.py` | open demo off by default (404), role-limited audited demo sessions, pending accounts excluded, cannot enable without TEST DATA mode |
| `test_ai.py` | Ask Motion AI with a mocked provider: tool loop + logging + audit, branch-scope and permission denials inside tools, unknown tools/bad arguments contained, data minimisation (no per-cashier/cost data sent), provider failure → rule-based fallback, hourly limit, cashier denied, bounded history, role-filtered tools, role-aware suggestions, section links limited to pages the user can open |
| `test_stage1_hardening.py` | Codex stage 1: demo isolation (C2), client deployment refuses demo switches, idle sign-out, approval/branch-scope hardening (C1, H1) |
| `test_my_access.py` | My access: each account sees exactly its own roles, branches, permissions (all labelled) and approval authority; Roles & permissions: the all-roles directory matches the enforced matrix, names of role holders only for company-wide admins; sign-in required |
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
Command: `cd web && E2E_CREDS=<creds.json> npx playwright test -c e2e/playwright.config.ts`. Result: **19 passed**:
- the six role dashboards render live data with no page errors (screenshots taken at desktop size, plus mobile
  for the Head Manager and Branch Manager);
- every navigation route renders for the Head Manager;
- a cashier completes a sale and gets a numbered receipt;
- a cashier is blocked from back-office pages.
- the company logo returns to the dashboard, and sign-out lands on the sign-in page;
- Ask Motion AI: the floating button opens the chat on any page, a question gets an answer, Escape closes it, and the Alerts & insights page renders;
- an accountant downloads an audited CSV report (POST export);
- My access on the till: the cashier sees their role, branch, permissions and "does not approve"; sections collapse and expand, and the panel closes;
- Roles & permissions: a cashier sees all eight roles (own role open and marked, others collapsed), opens the Head Manager card, sees no staff names, switches to Compare roles and back to My access; the Head Manager sees who holds each role;
- weighed refund: 0.300 kg sold and 0.100 kg refunded accepts exactly 0.2 and refuses 0.201;
- a page whose file cannot load (blocked twice) shows "This page could not be loaded" with Try again, which recovers once the file is reachable;
- phone size (390 × 844, `mobile.spec.ts`): the till, My access, the Compare roles table (scrolls inside its box), the manager dashboard and the Ask Motion AI chat fit without horizontal scrolling.

Screenshot review found grey charts (chart colour tokens weren't emitted by Tailwind). Fixed with `@theme static`.

## Final live browser round (16 Nov 2026, Claude in Chrome, https://motion-supermarket.onrender.com)
Every step ran in the real browser against the deployed TEST site, as the demo account for that role.

| Area | What was done live | Result |
| --- | --- | --- |
| Sign-in and idle | Demo picker grouped by role. A tab left idle for over 5 minutes | Picker correct. The idle tab redirected to `/login?reason=idle` |
| Permissions | Every demo account requested every screen's API. A permission matrix was compiled | Only 200/403, never 5xx. Each role sees exactly its matrix (GM: no ledger; Ops/Inventory: no finance; branch managers: own branch only; cashier: till only). `sys.admin` is not offered in the open demo, by design |
| My access | Opened from the account menu (Head Manager, cashier on the till) | Roles, branches, approval authority and permissions by area. Collapse and expand both work |
| Ask Motion AI (DeepSeek) | Head Manager margin by branch; Inventory Manager asked for profit; CEN branch manager asked about North; cashier | Per-branch P&L tools and an "Open Profit & cash" link. Profit politely withheld. North refused (out of scope). Cashier 403. Every question audited |
| Till | Weighed and unit items, cash with change, refund request, second open refund on the same receipt, manager approval, cash pay-out, blind close, manager reconcile | Receipt NTH-T1-000196 (VAT 1,373). Duplicate refund refused. Refund NTH-T1-000197 −4,500 with the original VAT (−686). Blind close, variance −32,000 reconciled |
| Procurement and payables | Final approval PR-000010, PO issued to the approved supplier, partial receipt (100 of 120), duplicate supplier ref (case-insensitive), bill posted, partial payment (self-approval refused), balance received | PO 6 matched. Duplicate refused. SINV2026-00021 posted. Open balance 1,482,800 from Tryton. Second draft bill shows 408,000 |
| Transfers | Request, approve, dispatch, receive with explicit counts (Confirm disabled until all counted), dispatcher tried to receive, late arrival, write-off, closed | TRF-000005 closed. Shortage resolved as found; damage written off via WO-000051 |
| Barcodes | Bad check digit, valid EAN-13 registered, scanned at the till | Bad digit refused (client and server). Scan added the product |
| Exports | Finance P&L export | POST 200 with a CSV |
| Audit | Searched invoice.post, pos.refund and ai.ask | Events present, including the acknowledged tax warning |

**Defects found live and fixed in this round:**
1. A supplier bill for packaged groceries could not be posted, because Tryton's default-tax warning was restored.
2. Draft bills showed UGX 0.
3. A refund quantity of 0.5 kg and a 12.5% discount could not be typed.
4. The refund approval showed "Line 3503" instead of the product name.
5. After a deploy, an open tab showed a blank page.
6. The receiving branch could not see or resolve its transfer discrepancies.
7. The dispatcher saw a receive form they were not allowed to use.
8. CSV exports on the Finance, Shifts and Stock pages used GET links, which the new POST-only endpoints rejected (found in code review).

Each fix is deployed and was re-verified live.

## Not yet executed
- Load test for 10 users / 4 cashiers on Render. Locally, a concurrent two-cashier race is tested. Throughput on
  Render's Starter plan has not been measured.
- Physical-hardware tests (printers, scanners, drawers, UPS).
