# Response to the Codex audit (`CLAUDE_PRODUCTION_HANDOFF.md`)

The audit reviewed commit `7f2fe8e`. This file records how each finding is handled, so the next audit can verify
it. Status values: **fixed** (code and regression test), **partial**, **planned** (next stage), **decision**
(needs a business or Finance decision).

## Stage 1
| ID | Status | What changed | Regression tests |
|---|---|---|---|
| C2 demo isolation | **fixed** | `MOTION_DEPLOYMENT` (client or demo); every demo/seed switch defaults off; a client deployment refuses to start with any of them on (`settings.py`). Only accounts flagged `is_demo` can use the picker (migration 0004). Sessions record `origin`, and demo sessions are refused and revoked (persisted in a separate transaction) as soon as open demo is off. `render.yaml` is now the client-safe template. | `test_stage1_hardening.py`: demo allowlist, revocation, startup refusal ×3, client defaults |
| C2 credentials | **decision / operator** | Rotate the Neon password and Render API key in their consoles, then update `DATABASE_URL` on Render. | — |
| C1 approval correction | **fixed (correction disabled)** | Generic `resubmit` now returns 409. Corrections are a cancel plus a new request through the original typed endpoint, so validation and amounts are re-derived. Execution re-validates against server records: refund lines must belong to the approved receipt (once each, still refundable, amount recomputed); a transit write-off must still own its discrepancy and the units must still be in transit; a shelf write-off rechecks stock under the checkout guard. | `test_returned_request_cannot_be_edited_generically`, `test_writeoff_rechecks_stock_at_execution`, `test_rejection_and_return_need_reasons` |
| H1 branch scope | **fixed (listed boundaries)** | Trial balance, capital recovery and investments are HQ-only. Unallocated bills are HQ-only, scope is checked before any state is read, and payment and expense execution check scope first. Initiators need current scope to see their requests. The "other pending" inbox shows only requests the person raised or could act on. A delegation needs the delegator to cover the request's branch. Policy, delegation and user administration are company-wide. Device register/revoke/list/events are branch-gated. Requisition evidence and insight transfer suggestions only include in-scope branches. Insight acknowledgement requires the insight to be visible. Audit filter choices come from in-scope events. | `test_company_wide_reports_and_admin_need_all_branches`, `test_payment_request_checks_branch_before_revealing_state`, `test_initiator_loses_visibility_with_branch_scope` |
| L1 (part) session expiry UI | **fixed** | A 401 on any data request clears client state and reloads to sign-in; 401s from identity probes are ignored (this fixed a stuck "Loading" that Codex also saw at the cashier redirect). | E2E: cashier sale, logo + sign-out |
| M7 (part) E2E races | **fixed** | The cashier spec waits for the shift form or the till; the navigation spec asserts the expected link set first. | 10/10 Playwright |

User requests delivered in the same change: the logo returns to your dashboard; sign-out reloads to the sign-in
page; automatic sign-out after 5 idle minutes (browser and server); the demo picker is grouped by role family;
barcode registration, validation and scanning (see `docs/barcode-scanners.md`).

## Stage 2 (part 1: money)
| ID | Status | What changed | Regression tests |
| --- | --- | --- | --- |
| H2 idempotency | **fixed** | One contract (`services/idempotency.py`, migration 0006 `motion.operation`) for POS sale, refund pay-out and bank deposit. Each key is scoped to operation + user and carries a request fingerprint and the result. The key is resolved before stock guards, line counters, approvals or the shift are touched. Same key + same request replays the receipt; same key + a different request returns 409. The row commits with the business result, so a simultaneous duplicate waits, fails serialization and replays. `post_sale` no longer returns an earlier row for a reused key. The till keeps one key per basket across failures and reloads (15-minute window), and a refund pay-out uses a stable key per approval. A bank slip reference posts only once per branch. | `test_stage2_money.py`: replay, changed payload, per-cashier scope, two-thread duplicate, refund replay with one approval transition, deposit replay and unique slip |
| H3 line calculator | **fixed** | One rule, quantity × unit price rounded half-even like Tryton: `common.line_amount`, `web/src/lib/money.ts` and the offline till use it. Posting asserts Tryton's line amount equals it. Each sale line stores gross, VAT and the original tax ids, plus refunded gross and VAT (backfilled; all 1,352 dev sales reconcile). Partial refunds take a proportional share, and the last one closes the line's remaining gross and VAT exactly. Refunds post the original taxes (Tryton `motion_line_taxes` override). Only one open refund per receipt, the receipt row is locked, and duplicate lines are refused. The pay-out must match the approved per-line amounts. Current-cost COGS on returns is unchanged (documented policy). | `test_weighted_line_uses_the_same_rounding_as_tryton`, `test_partial_refunds_close_the_line_exactly`, `test_one_open_refund_per_receipt_and_no_duplicate_lines`, `test_refund_reverses_the_original_vat_not_todays_rules` |
| H3 refund tender | **fixed (interim policy)** | Tryton only allows cash pay-outs at the till. The till refunds cash up to the cash taken on that receipt, minus earlier refunds. A mobile-money or card portion is refused at request time and must be refunded by Finance through the provider, so an unverified claim never becomes till cash. **Finance to confirm.** | `test_till_refunds_cash_only_up_to_the_cash_paid` |
| H10 shift close | **fixed (online)** | Checkout and refund bump the open shift's `activity_seq` (migration 0006), so they serialize with close and force-close: a sale is either counted in the close or refused with "open a shift". Cash payments must land in the shift's own Tryton cash session. The cashier's open-shift view hides takings and expected cash until the blind count; supervisors and the close result still show them. Late offline events still post with no shift (cash custody: **Finance decision**, unchanged). | `test_close_and_checkout_serialize`; blind-count assertion in `test_shift_close_books_variance_to_over_short` |

## Stage 2 (part 2) and Stage 3: Codex follow-up, reviewed by Claude
Codex implemented the remaining engineering items (see `CLAUDE_FOLLOWUP_HANDOFF.md`). Claude reviewed the full diff, fixed
what is listed under "review fixes", and re-ran the suites.

| ID | Status | What changed |
| --- | --- | --- |
| H2 (complete) | **fixed** | Money keys are reserved in `motion.operation` before any business write and completed in the same transaction (migration 0007). A reservation without a result returns 409 for manager reconciliation. Safe movements serialize per branch (`motion.safe_guard`). A bank deposit checks the safe balance on its date and every later date, and future-dated deposits are refused. |
| H4 transfers and stock | **fixed** | Every dispatched product needs an explicit received count (zero allowed). The UI disables "Confirm receipt" until all are counted. Duplicate and foreign lines are refused. One stock guard (`common.lock_stock`) is shared by POS, dispatch, receipt, late receipt and write-offs. Sibling discrepancy resolution locks the parent transfer first. Late receipts assert the Tryton move state. |
| H5 purchasing and payables | **fixed** | The PO is posted to the approved supplier ID, which must be active and have an invoice address. Receipts allocate across the PO line's own pending moves. Invoices match fulfilment moves and unbilled quantities per PO line (`_invoice_matches`). A supplier invoice number posts once per supplier (`motion.supplier_invoice_ref`). Open balances come from Tryton `amount_to_pay`. Payment execution re-checks the bill, the branch and the approved amount. |
| H6 warnings | **fixed** | Tryton warnings are restored. Only the exact audited transfer-quantity warning is acknowledged. |
| H7 till step-up | **fixed** | The manager override uses the central credential check and lockout. It is bound to the exact priced basket, shift, cashier and register, and is refused if the manager must change their password. The client IP comes from uvicorn's trusted-proxy handling (`FORWARDED_ALLOW_IPS`), not a raw header. |
| H8/H9 edge and sync | **fixed (code)** | Events are validated and replay is bound to device and payload. Rejections persist, with retryable and terminal outcomes kept separate. Batch retries are bounded. Sold units stay reserved until the snapshot has the receipts. The edge till needs HTTPS off loopback and rate-limits offline sign-in, and its JavaScript moved to `till.js` with DOM-text rendering. |
| M1–M6 | **fixed** | SQL-side branch scoping before row limits. The fiscal-year start comes from Tryton. Hourly reports use the original offline time. Capital recovery includes zero-flow weeks and stops at recovery. Session activity refreshes outside the business transaction. `/readyz`, startup schema checks and a cross-process bootstrap lock were added. CSV exports are CSRF-protected POSTs with formula escaping. |
| M7 CI | **added** | `.github/workflows/verify.yml`: ruff, pytest on PostgreSQL 18, typecheck, build and Playwright. |

**Review fixes by Claude**
- The Finance, Shifts and Stock pages still used GET links for CSV exports, which are now POST-only (405). They now use a
  shared `CsvButton` (POST, CSRF, error toast).
- `--forwarded-allow-ips='*'` was removed from the Dockerfile. The TEST service sets `FORWARDED_ALLOW_IPS=*` (Render is the
  only ingress). Otherwise every user would share the proxy IP for sign-in rate limits and audit. A client deployment
  should list its verified ingress addresses.
- The TEST demo service keeps `MOTION_AUTO_BOOTSTRAP=1` and its demo switches on Render. `render.yaml` stays the client-safe
  template, where bootstrap is a separate migration job.

**Fixes from the final live browser round** (see `docs/test-report.md`):
- H6 follow-up: posting a 3-way-matched bill acknowledges only Tryton's `invoice_taxes` warning for that bill (taxes
  follow the approved PO), and the audit records which lines. Regression test included.
- Draft and validated bills show live totals instead of an empty cache.
- Transfer discrepancies are visible to the receiving branch, which records late arrivals. The dispatcher sees a
  notice instead of the receive form.
- Till inputs accept decimals (refund kg, discount %). Refund approvals name the products.
- After a deploy, the web app reloads once if a page chunk from the previous build is gone, instead of
  showing a blank page.

**New feature: My access.** Every account opens **My access** from the account menu (also on the till). It shows the
account's roles, branches, approval authority and all permissions grouped by area, with collapsible sections. The
data comes from `GET /api/auth/access`, built from `permissions.py`. Tests: `test_my_access.py` and the E2E spec "My access …".

## Still open (decisions and operations, not code)
- **Finance:**
  - refund-to-tender policy;
  - cash custody and clearing for late offline receipts;
  - a reconciliation workflow for terminal sync rejects;
  - tax and EFRIS;
  - period close and opening balances.
- **Identity:**
  - MFA/SSO for privileged users;
  - device-bound offline credentials in place of password verifiers on edge stations.
- **Operations:**
  - rotate the Neon, Render and DeepSeek keys;
  - run a staging restore with migration 0007;
  - load test with 10 users;
  - verify TLS and ACLs on real branch hardware.
- **Cleanup (L1/L2):**
  - split `services/pos.py` and the operations router;
  - typed API responses;
  - fewer broad frontend casts.
