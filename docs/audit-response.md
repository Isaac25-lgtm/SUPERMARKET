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

## Next stages (planned, in the audit's order)
- **Stage 2, money and inventory:**
  - H2: one idempotency contract (operation key + fingerprint + result) for sale, refund and bank deposit.
  - H3: one canonical line calculator shared by preview and posting; per-line refundable gross/net/VAT balances.
  - H4: explicit transfer counts, shared stock guards, Tryton state assertions on late receipts.
  - H5: receipt-level invoice allocation, supplier-reference duplicate check, Tryton-derived open balances.
  - H10: shift-close serialization, and expected cash hidden before the blind count.
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
