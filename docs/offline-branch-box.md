# Offline selling: the branch box

Decisions (16 Nov 2026, with the client's representative):

| Question | Decision |
|---|---|
| Where it runs | The branch's **back-office PC** (Windows), as a background program that starts with Windows. A UPS is strongly advised. |
| Checkout computers | Nothing installed: a desktop shortcut opens the till from the box (Edge/Chrome app window, silent printing to the USB receipt printer). 3–5 tills per branch. |
| Offline limit | **24 hours** after the last refresh from head office (prices, products, staff access). |
| Refunds and discounts above the cashier limit offline | Approved **in person by the branch manager** typing their own username and password on the till. |
| Cards | Standalone bank card machines: the cashier records "card" and the reference. |
| Mobile money | Recorded with its reference as "to be verified" (Finance reconciles). |
| EFRIS | Unknown; nothing in the design blocks adding e-receipts later. |

## How it works

```
 checkout PCs (browser)  --HTTPS on the shop LAN-->  branch box (back-office PC)  --HTTPS-->  head office (Render)
   same React till screen                            SQLite outbox, till API                   /api/edge/* (device token)
```

* **Tills always go through the box**, online or not, so cashiers never switch anything. The box serves the
  same React build (`web/dist`) and answers the till's API paths (`branch-edge/motion_edge/webapi.py`); the
  app sees `meta.edge = true` and shows only the till.
* **Every action is an immutable outbox event** in SQLite (WAL, `synchronous=FULL`, one `BEGIN IMMEDIATE`
  per action, so 3–5 tills are serialised and the last unit cannot be sold twice):
  `shift_open`, `sale`, `refund`, `shift_close`.
* **Live forwarding.** Each recorded event wakes the sync loop, so with internet a sale reaches head office
  within seconds. The loop also checks in every 5 s (heartbeat) and backs off to at most 60 s while offline.
  Prices, products, barcodes, staff grants and open-till information refresh every 5 minutes.
* **Head office replays events through its own rules** (`api/motion_api/services/sync.py`), each in its own
  transaction, exactly once (UUID in `motion.sync_inbox`):
  * `shift_open` → `pos.open_shift` (cash session, float adjustment), as the cashier, with the local time.
  * `sale` → `pos.post_sale` on that shift; an in-person discount approval is accepted only if the named
    manager holds `pos.override.approve` for the branch, otherwise it is flagged.
  * `refund` → `pos.request_refund` (cashier) → `approvals.act` (the manager who approved at the till) →
    `pos.execute_refund`; amounts follow the same rule as online (proportional share, the last units close
    the line exactly), cash only and never more than the cash taken on the receipt.
  * `shift_close` → `pos.close_shift` (blind count); head office recomputes expected cash and flags a
    difference from the box's figure.
  * An event that arrives before what it depends on (its shift opening, the original sale) is answered
    `retry`, not rejected. Problems that need a person become `sync_conflict` discrepancies (dashboard
    exceptions) or rejected events (shown as "N need review" on the till and in System > Branch boxes).
* **Guards at the box:** only cashiers open shifts (managers approve on the cashier's till); a till that still
  has an online shift open at head office cannot get a second shift; manager approvals are single use and
  tied to the basket; wrong manager passwords lock out after 5 tries in 15 minutes; after 24 hours without a
  refresh the box refuses to open shifts or sell.
* **Receipts** are numbered per till by the box (`NTH-T1-B000123`); head office assigns its own number on
  upload and the box shows it on reprints. Refunds at the box are for receipts recorded by this box; older
  or other-branch receipts are refunded online through the approval inbox.

## Certificates and till trust
* On first start the box creates a **trust root** (20 years). Its private key is used once to sign an
  **issuer** certificate and is never written to disk. The tills install only the root (after comparing its
  fingerprint), so its fingerprint never changes for the life of the box.
* The **issuer** (kept on the box, `box-issuer*.pem`) signs the **server certificate** for the PC's names and
  current private LAN addresses. The server certificate is reissued automatically when the PC's address
  changes (DHCP) or 30 days before it expires (825 days); the tills keep working because they trust the root.
* Root and issuer are **name-constrained** to the PC's names and the private ranges 10/8, 172.16/12,
  192.168/16, 127/8. A stolen box yields only the issuer key. Verified: OpenSSL rejects a public-site
  certificate forged with the issuer key (`test_branch_box_cert.py`). Windows was **not** verified here:
  without the root installed, Windows reports only "untrusted root" and shows no name-constraint result,
  and installing a root needs Administrator. Check it at the pilot (below).
* Tills use the **PC name** (`setup-till.ps1` asks the box for it); reserve the back-office PC's address on the
  router anyway. `check-till.ps1` connects exactly as the till does (Windows trust + name) and needs no
  Administrator: run it after setup, after a reboot and after an address change.

## Uninstall safety
`MotionBranchBox.exe --outbox-status` reads the saved events read-only (box running or stopped) and exits
0 (everything accepted by head office), 3 (pending upload or awaiting review) or 2 (unreadable).
`uninstall-box.ps1` runs it first: it removes nothing while anything is pending or awaiting review (unless
`-Force`, which removes only the program), keeps the data whenever the check cannot run, deletes the data with
`-RemoveData` only when everything was accepted, and drops unsent sales only with an explicit `-ForceRemoveData`.
`test_uninstall_script.py` runs the real script and exe against disposable folders for all of these cases.

## Install (per branch)

Build once: `powershell -ExecutionPolicy Bypass -File branch-edge\windows\build.ps1` →
`branch-edge\windows\dist\MotionBranchBox-<version>.zip` (PyInstaller folder: `MotionBranchBox.exe`, the
till screen in `web\`, and the scripts). Plain-language steps are in `branch-edge/windows/README.txt`:

1. Head office: System → Branch boxes → Register branch box → copy the token (shown once).
2. Back-office PC: `install-box.ps1` (Administrator). Installs to `C:\Program Files\MotionBranchBox`, settings
   and data in `C:\ProgramData\MotionBranchBox` (ACL: SYSTEM + Administrators), a startup task running as
   SYSTEM that restarts on failure, and a firewall rule for ports 8443/8080 on private/domain networks only.
   On first start the box creates its own certificate (10 years, the PC's names and LAN addresses) and prints
   its SHA-256 fingerprint.
3. Each checkout PC: `setup-till.ps1` (Administrator). Downloads the box certificate from
   `http://<box>:8080/cert`, shows the fingerprint to compare with the back-office PC, trusts it, checks
   `https://<box>:8443/`, and creates the "Motion Till" shortcut.

## Test it

* `api/tests/test_branch_box_cert.py`: trust root stable across address changes, reissued server certificate,
  real TLS handshakes by name and address, forged public-site certificates refused, root key never stored,
  `--outbox-status` for empty / accepted / pending / awaiting review / unreadable.
* `api/tests/test_uninstall_script.py` (Windows with the built exe): the real uninstaller against disposable
  folders.
* `api/tests/test_branch_box.py`: the till API over HTTPS with head office unplugged: shift, three sales
  (weighed + in-person discount approval, card + mobile money), refund approved in person, blind close;
  reconnect; head office has the shift (expected cash, variance), all four transactions on it, the refund's
  approval, stock, no duplicates on resend; plus the role rules, the 24-hour limit, dependency retry and the
  open-till guard. `api/tests/test_offline.py` keeps the earlier exactly-once and concurrency tests.
* **Outage drill** (real processes and a browser):
  `python branch-edge/tools/outage_drill.py --creds <creds.json> [--box-exe ...\MotionBranchBox.exe]`
  registers a box at the local head office, runs it over HTTPS, routes it through `tools/wan_relay.py` (a
  switchable "internet line"), and runs `web/e2e/branch-box.spec.ts`: sell online → cut the line → sell
  (weighed item with manager-approved discount, another sale) and refund with the manager present → reconnect
  → everything uploads → close the shift → head office shows the sale on the box's shift. Passed with both
  `python -m motion_edge` and the packaged exe (16 Nov 2026). The browser part skips certificate checks, so the
  drill separately verifies TLS without bypass (trusting only the box root, by PC name and by address).

## Pilot checklist (one branch)
1. Install the box (`install-box.ps1`) and two tills (`setup-till.ps1`), with the real accounts. Reserve the box
   PC's address on the router. Confirm: startup after reboot, Windows Firewall profile of the shop network is
   Private, the fingerprint matches, the "Motion Till" shortcut opens, receipts print silently on USB.
2. `check-till.ps1 -Box <PC name>` on each till: after setup, after rebooting the box, and after changing the box
   PC's address (DHCP release/renew or a different reservation). All must print OK.
3. Windows name constraints: on the box (Administrator) `python forge_test_cert.py C:\ProgramData\MotionBranchBox
   forged.pem`; on a till `verify-cert-windows.ps1 -Installed -Chain forged.pem` must print **INVALID** and
   `-Installed -Chain box-server-chain.pem` must print **OK**. If the forged one is OK, stop and escalate.
4. Two tills in the same WAN outage: weighed sale with manager override, a refund, reconnect while events are
   queued. Reconcile each receipt and shift at head office against the local outbox (once in stock, cash, ledger).
5. Power-cycle the box on the UPS during queued events: they survive and resume uploading.
6. Show a manager and head office how a rejected event is identified and resolved without editing the original;
   agree who owns cash from sales that upload after the shift closed.

## Not done yet / to verify on site

* Not yet installed on a real branch PC: the install scripts were written for Windows PowerShell 5.1 and
  the exe was built and run on the development PC, but admin-only steps (startup task, firewall, certificate
  store, shortcut) still need a first run at a pilot branch.
* No one-click `Setup.exe` (needs Inno Setup or similar on the build PC); the zip + scripts work without it.
* EFRIS e-receipts, cash-drawer kick and direct ESC/POS printing (the till prints through the browser).
* Finance decision still open: who handles cash from offline sales that upload after the shift is closed.
