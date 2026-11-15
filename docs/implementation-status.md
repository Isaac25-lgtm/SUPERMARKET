# Implementation status (honest inventory)

Legend: ✅ working & covered by automated tests · ☑️ working, verified manually or by build only · ⚠️ partial · ❌ not implemented

## Platform
| Item | Status | Notes |
|---|---|---|
| Tryton 8.0.14 engine, 15 official modules + `motion_branch` | ✅ | idempotent bootstrap; module updates only when versions change |
| Neon/Render configuration (`DATABASE_URL`, direct endpoint, search_path pin) | ☑️ | live deploy created; see README |
| Sessions, CSRF, lockout, suspension, password change | ✅ | |
| RBAC + branch scope on all endpoints | ✅ | matrix generated from code |
| Append-only audit + approval history (DB triggers) | ✅ | owner DB role can still disable triggers → use a non-owner role in production |
| MFA | ❌ | |
| Open demo (password-less picker) | ✅ | `MOTION_OPEN_DEMO`, refused unless TEST DATA mode; ON for the live TEST site |

## Operations
| Item | Status | Notes |
|---|---|---|
| POS checkout (VAT, discounts, split tenders, change, receipts) | ✅ | |
| Scale labels / sold-by-weight foodstuff | ✅ | |
| Oversell protection under concurrency | ✅ | |
| Manager override (single-use) | ✅ | |
| Refunds with approval, no duplicate payout | ✅ | |
| Shifts: float, blind count, variance → over/short, reconcile, force-close | ✅ | |
| Products, prices (audited), barcodes, reorder points, batches/expiry | ✅ / ⚠️ | batch remaining is a FEFO *estimate* (POS does not record lots) |
| Stock counts → discrepancies | ☑️ | |
| Inter-branch transfers via transit, discrepancies, write-off, late receipt | ✅ | |
| Procurement bands → PO → partial receipt → 3-way match → post → payment approval → payment | ✅ | |
| Expenses (accrual) and payment | ✅ | |
| Document attachments (invoices/expenses) | ❌ | reference number captured instead |
| Supplier/customer master-data UI | ⚠️ | suppliers are selectable; there is no supplier create/edit screen yet |

## Finance & intelligence
| Item | Status | Notes |
|---|---|---|
| Branch & consolidated P&L, cash, payables, journal, trial balance | ✅ | |
| Bank deposits (safe → bank) | ✅ | API + seeded weekly deposits; no dedicated UI screen yet (API only) |
| Break-even, capital recovery | ✅ | |
| Period close UI, opening-balance / historical import | ❌ | Tryton supports period closing; no UI here |
| Rule-based insights, ask-Motion (deterministic) | ✅ | |
| LLM assistant | ❌ | intentionally off; no adapter shipped |
| CSV exports (P&L, stock, shifts, audit) | ✅ | PDF export not implemented |

## Offline
| Item | Status | Notes |
|---|---|---|
| Edge snapshot, time-limited grants, offline sale, durable outbox, exactly-once sync, conflicts | ✅ | simulated WAN outage against the real central API |
| Edge LAN till page | ⚠️ | minimal: scan/search, cash sale, receipt number; no refunds, overrides or weighing UI beyond a prompt |
| Physical hardware (printers, scanners, drawers, UPS, real multi-site LAN) | ❌ | not tested |

## Frontend
All routes render real data from the API. Checks run: strict `tsc`, the production build, and 9 Playwright E2E
tests. There are no unit tests for UI components yet.
