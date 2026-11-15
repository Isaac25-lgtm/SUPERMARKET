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

## Next stages (planned, in the audit's order)
- **Stage 2, part 2 (inventory and purchasing):**
  - H4: explicit transfer counts, shared stock guards, Tryton state assertions on late receipts.
  - H5: receipt-level invoice allocation, supplier-reference duplicate check, Tryton-derived open balances.
- **Stage 3:**
  - H6: replace blanket `_skip_warnings` with classified, authorized confirmations.
  - H7: step-up manager authentication bound to the exact cart, with trusted proxy configuration.
  - H8/H9: versioned edge sync contract, durable central rejects, TLS and protected edge credentials, DOM-safe till.
  - M1–M6: edge watermarks, fiscal periods, SQL-side scoping and pagination, lock learning and session writes, a
    split migration role and readiness probe, CSV formula safety and POST exports.
- **Stage 4:** typed API contracts and UI module splits (L1, L2, and the cleanup items).
- **Decisions needed from Finance or the business (M8, H3, H10):**
  - refund-to-tender rules;
  - cash custody for late offline events;
  - tax configuration;
  - period close and opening balances;
  - MFA/SSO for privileged users.
