# Audit handoff — for Codex (or any reviewer)

You are auditing a **multi-branch supermarket platform**. Tryton 8.0 LTS is the in-process accounting/stock/POS
engine, FastAPI is the policy layer, and React is the UI. It is deployed on Render with a Neon PostgreSQL database.
Please review it for **correctness, security and financial integrity**, and report findings with file:line
references and a severity rating. Fixes are welcome as separate, minimal, tested commits.

**No credentials are provided or needed.** Do not look for, request or use any account passwords, API keys or the
production database string. Do **not** connect to Neon or Render. All testing runs against a disposable local
PostgreSQL that the scripts create.

---

## 1. Orientation (read in this order)
1. `README.md`: what the system does, the run commands, the status.
2. `docs/architecture.md`: the decision records (why in-process Tryton, the transaction model, branch dimension,
   offline design).
3. `docs/accounting-policy.md`: the posting rules and report definitions you should verify.
4. `docs/role-permissions-matrix.md` (generated from `api/motion_api/permissions.py`).
5. `docs/implementation-status.md`: what is deliberately unfinished (don't report these as bugs unless they're
   unsafe).

| Area | Files |
|---|---|
| Transaction gateway (retries, lock protocol, context) | `api/motion_api/tryton.py` |
| Auth, sessions, CSRF, lockout, open demo | `api/motion_api/security.py`, `api/motion_api/web.py`, `api/motion_api/routes/core.py` |
| Authorization matrix | `api/motion_api/permissions.py` |
| POS (checkout, refunds, shifts, overrides) | `api/motion_api/services/pos.py`, `services/catalog.py` |
| Approvals engine | `api/motion_api/services/approvals.py` |
| Stock & transfers | `api/motion_api/services/stock.py` |
| Procurement, invoices, payments, expenses | `api/motion_api/services/purchasing.py` |
| Reporting (P&L, break-even, payback, bank deposits) | `api/motion_api/services/finance.py`, `services/dashboards.py` |
| Insights | `api/motion_api/services/insights.py` |
| Offline: central side / edge side | `api/motion_api/services/sync.py` / `branch-edge/motion_edge/*` |
| Custom Tryton module | `tryton-modules/motion_branch/motion_branch/branch.py` |
| Schema, triggers | `api/motion_api/migrations/*.sql` |
| Bootstrap / base setup / TEST seed | `api/motion_api/bootstrap.py`, `setup_base.py`, `seed*.py` |
| Deployment | `Dockerfile`, `render.yaml`, `.dockerignore` |
| Tests | `api/tests/*` (integration), `web/e2e/*` (Playwright) |

## 2. Run it (local, disposable)
```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt     # Linux/macOS: .venv/bin/...
bash infra/scripts/dev-db.sh init && bash infra/scripts/dev-db.sh start       # own cluster on 127.0.0.1:55432
echo "MOTION_SEED_PASSWORD_SECRET=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env.dev
MOTION_ENV_FILE=.env.dev PYTHONPATH=api .venv/Scripts/python -m pytest -q      # expect 68 passed
.venv/Scripts/ruff check api branch-edge tryton-modules
(cd web && npm ci && npx tsc -b --noEmit && npm run build)
```
The test harness refuses non-local databases. To explore the UI, follow "Run it locally" in the README and set
`MOTION_OPEN_DEMO=1` in `.env.dev`. You then get a password-less account picker, because the seed's TEST
accounts have passwords nobody is meant to know.

## 3. Invariants that must hold (please try to break them)
**Ledger**
- [ ] Every posted journal entry balances; the trial balance balances.
- [ ] Every journal entry the platform creates carries a branch; Σ branch P&L = consolidated, for any period.
- [ ] A POS sale posts exactly: cash/clearing = gross, revenue = ex-VAT, VAT liability = the difference, COGS =
  quantity × average cost. A refund reverses at the original line price.
- [ ] Internal transfers create **no** journal entry and **no** revenue. Origin + transit + destination
  quantities are conserved. A shortage stays in transit until an authorised write-off (shrinkage, not COGS) or a
  late receipt.
- [ ] Profit excludes the purchase-clearing timing account; capital recovery uses cash, not profit.

**POS**
- [ ] One idempotency key gives at most one sale.
- [ ] Concurrent sales never oversell when negative stock is disallowed (the `motion.stock_guard` row update must
  precede the stock read).
- [ ] An override is single-use, bound to register + cashier + lines, and expires after 30 minutes.
- [ ] A refund can't exceed what's refundable, and a refund approval can't be paid out twice (DB CHECK plus
  consumption).

**Approvals**
- [ ] Default deny: no matching policy band means no request can be submitted.
- [ ] No self-approval; one person per step; strict step order.
- [ ] Branch scope and role (or a live delegation within its ceiling) are required at every step.

**Security**
- [ ] Every non-public endpoint authenticates and checks permission + branch scope **server-side**. Look for any
  endpoint or service function that reads a record by id without checking that record's branch (IDOR).
- [ ] CSRF is required on every state-changing request.
- [ ] Sessions are revoked on suspension, access change and password reset.
- [ ] Session tokens are stored only as hashes.
- [ ] Audit events and approval history can't be altered through the app (DB triggers).
- [ ] No secret is logged, returned or committed. Run a secret scan over the git history.

**Offline**
- [ ] Events are ingested exactly once.
- [ ] Conflicts are recorded and never dropped.
- [ ] Grants expire, and a suspended user is removed on the next sync.

## 4. Areas the author is least sure about (please prioritise)
1. **Authorization lives only in FastAPI.** Tryton runs in-process with access checks off (Tryton's RPC isn't
   exposed). Any service path that forgets `require*` is a hole. Audit `routes/*.py` and `services/*.py`
   systematically, e.g. `catalog.list_products` with `branch_id=None`, and `approvals.get` visibility.
2. **`_skip_warnings` is set globally** in `services/common.tryton_context()`, which suppresses every Tryton
   `UserWarning`. Are any of those warnings safety checks that should block?
3. **Learned lock extras** (`tryton.run(op_key=...)`): record-lock sets grow per operation and could serialise
   more than necessary, or go stale. Check correctness under multiple registers and fiscal years.
4. **Serialization retries** re-run whole units of work. Check that nothing outside the DB transaction (e.g.
   `pos._DEFERRED`, which is used only by the seed) leaks state between attempts.
5. **`last_seen_at` session touch** inside every unit of work: could concurrent requests from one session cause
   needless retries or deadlocks?
6. **Edge grants carry Argon2 password verifiers** to the on-premise device (needed for offline sign-in). Assess
   the exposure and mitigations: grant expiry, device revocation, file permissions.
7. **Open demo** (`MOTION_OPEN_DEMO`) is ON for the public TEST site. Verify it can't be enabled without
   `MOTION_DEMO_MODE`, and that demo sessions get no more rights than the chosen account.
8. **CSV exports use GET without CSRF** (they write an audit row). Assess the risk.
9. **Reporting SQL** in `finance.py` and `dashboards.py`: check branch filters, `NULL`-branch handling for
   non-HQ users, and period boundaries (business dates in Africa/Kampala vs UTC timestamps).
10. **Average-cost refunds**: COGS is reversed at the *current* average cost, not the original sale cost.
    Acceptable or misleading?
11. **`receive_goods`** pending-move lookup and partial-receipt handling against Tryton purchase processing.
12. **Insights** compute per request over all products × branches. Check performance on Render's 512 MB / shared
    CPU.
13. The **Dockerfile / Render** path: single worker, background bootstrap thread, health semantics while seeding.

## 5. Known limitations (by design for this test system; see `docs/implementation-status.md`)
- VAT rates are illustrative, there's no EFRIS integration, and income tax isn't modelled.
- No MFA, no document attachments, no opening-balance import, no supplier master-data screen, no bank-deposit UI
  (API only).
- The edge till page is minimal; refunds and overrides are online-only.
- The database runs as the Neon owner role, which can disable triggers; production should use a restricted role.

## 6. Reporting format
For each finding: **title · severity (critical/high/medium/low) · file:line · scenario to reproduce · impact ·
suggested fix**. Group by: financial integrity, authorization/security, concurrency, offline, reliability/ops,
UI/UX. For fixes, add a regression test in `api/tests/` and keep the 68 existing tests green.
