# Handoff to Codex: work since your last round (Claude, 16 Nov 2026)

Your previous handoff was `CLAUDE_FOLLOWUP_HANDOFF.md` (stage 2b/3 hardening). Claude reviewed it, deployed it, ran a
live browser round, then did the work below with the product owner. Everything is on `main` and deployed to the
TEST service unless marked otherwise. Start with **Rules**, then **Open items** for what to pick up next.

## Rules (unchanged, still binding)

- **Secrets:** never commit, print or log the Neon connection string, Render API key, DeepSeek key or seed secret.
  They live only in the git-ignored `.env.deploy` / `.env.dev` and in Render env vars. Scan diffs before committing.
  The owner has been asked to rotate all three keys; treat them as compromised until they confirm.
- **No credentials anywhere:** the live TEST site is an open demo (`MOTION_OPEN_DEMO=1`). Do not put passwords in
  docs, commits or chat. Demo passwords are derived from the seed secret (`python -m motion_api.creds` with
  `MOTION_ENV_FILE=.env.dev`); keep its output out of the repo.
- **Databases:** never touch the owner's PostgreSQL on 5432/5433. Destructive tests run only on the disposable
  cluster at `127.0.0.1:55432` (`.env.dev`).
- Pushing to `main` (Render auto-deploys `srv-dbsvv9dg1s2s738cbqn0`) is authorised. No force-push.
- DeepSeek receives only scoped, aggregated data; AI questions are audited.

## How to run things

```bash
# backend tests (disposable DB on 55432): 121 passed
MOTION_ENV_FILE=.env.dev PYTHONPATH=api .venv/Scripts/python -m pytest -q
# local server for E2E
MOTION_ENV_FILE=.env.dev PYTHONPATH=api .venv/Scripts/uvicorn motion_api.main:app --host 127.0.0.1 --port 8000
# browser tests (creds JSON = {username: password}, generated locally, never committed): 19 passed, 1 skipped
cd web && E2E_CREDS=<creds.json> npx playwright test -c e2e/playwright.config.ts
# branch box outage drill (real box process + switchable internet relay + browser): passed
python branch-edge/tools/outage_drill.py --creds <creds.json> [--box-exe branch-edge/windows/dist/MotionBranchBox/MotionBranchBox.exe]
# Windows package for the branch box
powershell -ExecutionPolicy Bypass -File branch-edge\windows\build.ps1   # -> branch-edge\windows\dist\MotionBranchBox-0.2.0.zip
```
Lint: `.venv/Scripts/ruff check api branch-edge tryton-modules`; web: `npx tsc -b --noEmit`, `npm run lint` (warnings
only, all pre-existing).

## What changed (commits on main after `803ad58`)

| Commit | Area | What and why |
|---|---|---|
| `db53f05` | Bills | Matched supplier bills posted despite Tryton's default-tax warning (classified skip). Draft bill totals computed live (`common.fill_invoice_totals`). **Superseded in part by `43ee261`.** |
| `878b1ac` | Till | Decimal refund quantities and discount % kept as typed text; refund approvals show product names. |
| `3a8d224` | Web | `lazyPage()` reloads once when a page chunk from an older build is missing (blank page after deploy). |
| `f4eec00` | Transfers | Receiving branch sees its transfer discrepancies (SQL scope on `tr.to_branch_id`); dispatcher cannot receive their own transfer and is told why. |
| `317f05b` | Tests/docs | Phone-size E2E (390×844), live round recorded in `docs/test-report.md`. |
| `ec3f9f9` | Access | **Roles & permissions**: `GET /api/auth/roles` (`permissions.roles_directory()`), "All roles" tab in the access panel (role cards + compare table). Names of role holders only for company-wide `admin.users`. |
| `fd9f09c` | UI | Clearer borders: darker `--color-line*` tokens; outer containers use `border-line-strong`. |
| `43ee261` | Review fixes | (1) **Bill VAT**: 3-way match also requires each line's taxes == PO line taxes and no manual tax lines; posting requires `supplier_total` (VAT incl.) == bill total; tax warning acknowledged only after that. (2) Weighed refunds compared in thousandths. (3) `PageErrorBoundary` around every page (Try again / Go to start). (4) Payables summary sums cached totals in SQL, computes only uncached drafts live. |
| `1e56fc5` | UI | Role icons (`components/RoleIcon.tsx`, `--color-role-*` tokens, `.role-tile`) on the picker, My access, Roles cards, account chip. |
| `ba5bfd2` | UI | Natural backdrop on sign-in and the demo account picker (`.nature-backdrop`, `components/NatureLeaves.tsx`). |
| (this commit) | **Offline** | **Branch box**: see below. |

## The branch box (offline selling): design you should know before changing it

Owner decisions: back-office PC (Windows) runs it; 3–5 tills per branch; **24 h** offline limit; refunds and
over-limit discounts approved **in person** by the branch manager on the till; standalone card machines; EFRIS
unknown. Full write-up: `docs/offline-branch-box.md` (read it first).

- **Tills always use the box** (online or not). The box serves the same React build and implements the till's
  API paths with the same shapes: `branch-edge/motion_edge/webapi.py` (routes, cookie session + CSRF double submit),
  `till.py` (shifts, catalog/lookup incl. scale labels, overrides, receipts, refunds), `service.py` (sale,
  outbox, sync), `store.py` (SQLite schema + migrations). `meta.edge` makes the web app show only the till
  (`App.tsx` Home → `/pos`, `AppShell` hides back-office items, `Pos.tsx` `BranchBoxPill` + `TillRefunds`).
- **Outbox events** (immutable, triggers): `shift_open`, `sale` (carries `shift_event_id`, optional `override`),
  `refund` (original event id, line numbers, manager), `shift_close` (counted, drop, box's expected cash).
- **Sync**: each event wakes the loop (`Syncer.wake`); heartbeat `GET /api/edge/heartbeat` every 5 s; snapshot every
  5 min (`MOTION_EDGE_PULL_SECONDS`); backoff max 60 s. `status()` uses `last_contact`.
- **Head office replay** (`api/motion_api/services/sync.py`): `_ingest_shift_open` → `pos.open_shift` as the
  cashier; `_ingest_sale` → `pos.post_sale` on that shift; override valid only if the manager has
  `pos.override.approve` + branch; `_ingest_refund` → `request_refund` → `approvals.act(manager)` →
  `execute_refund`; `_ingest_shift_close` → `pos.close_shift`, flags a different expected cash. Missing
  dependency → `sync.NotReady` → ack `retry` (handled in `routes/operations.py::_sync_event`). Snapshot now also
  carries `open_shifts`, `policy.vat_rate_pct`, `policy.scale_barcode`. `MOTION_OFFLINE_GRANT_HOURS` default 24.
- **Box guards**: only `cashier` opens shifts (branch managers have no `pos.shift` at head office); a till with
  another cashier's online shift cannot get a box shift; overrides single use + basket-bound; manager password
  lockout 5/15 min, checked outside the write lock; `check_fresh()` refuses after 24 h without a refresh; refunds
  only for receipts recorded by this box, cash only, ≤ cash taken.
- **Runtime/packaging**: `python -m motion_edge` (`__main__.py`): config from
  `%ProgramData%\MotionBranchBox\config.json`, self-signed cert (CA, **name-constrained** to the box's names/IPs),
  HTTPS 8443, plain HTTP 8080 serving only `/cert` and `/fingerprint`. `branch-edge/windows/`: `build.ps1`
  (PyInstaller folder build + zip), `install-box.ps1` (Program Files, ProgramData ACL, startup task as SYSTEM with
  restart, firewall private/domain), `setup-till.ps1` (cert trust after fingerprint check, Edge/Chrome app shortcut
  with `--kiosk-printing`), `uninstall-box.ps1`, `README.txt` (branch staff instructions).
- **Tests**: `api/tests/test_branch_box.py` (5), `api/tests/test_offline.py` (7), `web/e2e/branch-box.spec.ts`
  (run by the drill). `tools/wan_relay.py` is the switchable internet line used by the drill.

## Open items (priority order)

1. **Pilot install on a real branch PC.** The exe was built and passed the drill on the dev PC, but the admin
   steps in `install-box.ps1` / `setup-till.ps1` (scheduled task, firewall, LocalMachine\Root import, shortcut,
   Edge app window, silent printing to a USB receipt printer) have **not been run on a real machine**. Verify
   Chrome/Edge accept the name-constrained self-signed root; then a two-till outage drill and a power cut on the UPS.
2. **Review the box for security**: grants still carry argon2 verifiers of users' passwords (existing design; open
   item "device-bound offline credentials"); the box DB and `config.json` (device token) rely on the ProgramData
   ACL; the cert helper on :8080 is unauthenticated by design (public cert only) — the fingerprint comparison is
   the trust step.
3. **Conflict handling UX**: rejected box events show as "N need review" on the till and in System, and
   `sync_conflict` discrepancies on dashboards, but there is no dedicated review screen to resolve a rejected
   shift/refund (e.g. head office refused the shift → dependent refund rejected).
4. Box gaps: no sales history page at the box (`/sales` hidden), no reprint of older receipts, no cash-drawer kick or
   ESC/POS printing (browser printing only), no `Setup.exe` (needs Inno Setup), EFRIS e-receipts.
5. Finance decisions still open: refund-to-tender policy, cash custody for offline sales uploaded after a shift
   closed, purchase VAT configuration, tax/EFRIS, period close.
6. Operations: rotate the three keys; staging restore with migration 0007; load test (10 users, 4 cashiers); time
   the accountant dashboard with realistic bill volume; MFA/SSO for managers.

## Gotchas found this round

- `pos.open_shift` moves float differences through the branch **safe**; tests that open shifts with a large float
  drain the safe for later finance tests (use a zero float). `test_bank_deposit_moves_cash_from_safe_to_bank` used a
  fixed past date and silently skipped its main branch; it now deposits today.
- On Windows a venv `python.exe` is a launcher; `Popen.terminate()` leaves the real interpreter running. Use
  `taskkill /T` (see `outage_drill.stop`). Don't kill by name: the local uvicorn server is also `python.exe`.
- Windows PowerShell 5.1 turns native stderr into errors under `$ErrorActionPreference = "Stop"`; `build.ps1`
  wraps native calls in `Invoke-Native`.
- The dev DB's `sys.admin` had lost all-branch scope; the drill restores it (dev only) to register a device.
