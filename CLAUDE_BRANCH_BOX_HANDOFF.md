# Motion Branch Box review for Claude

> **Status (16 Nov 2026, Claude):** both "fix before the branch pilot" items are done (package 0.2.1).
> 1. Uninstall: `MotionBranchBox.exe --outbox-status` reads the outbox read-only (box stopped or running);
>    `uninstall-box.ps1` blocks on pending *and* rejected events, keeps data when the check cannot run, deletes
>    data only when everything was accepted or with explicit `-ForceRemoveData`. Verified by
>    `api/tests/test_uninstall_script.py` (real script + exe, disposable folders, all cases).
> 2. Till trust: stable trust root (key discarded) + name-constrained issuer + server certificate reissued on
>    address change; tills use the PC name; `check-till.ps1` for reboot/address-change checks; the drill now
>    verifies TLS without bypass. Finding while testing: Windows does not report name-constraint results for an
>    uninstalled root, so Windows enforcement could not be verified here (no Administrator); it is pilot step 3
>    in `docs/offline-branch-box.md`, with `forge_test_cert.py` + `verify-cert-windows.ps1 -Installed`.
> The pilot checks are in `docs/offline-branch-box.md` ("Pilot checklist"). `Pos.tsx` extraction is deferred,
> as suggested, until browser cases cover retries and receipt recovery.

16 November 2026. Reviewed `main` at `c995741`, including the branch-box implementation in `6c18f76` and the local `MotionBranchBox-0.2.0.zip`. Start with [CODEX_HANDOFF.md](CODEX_HANDOFF.md) for the design and [docs/offline-branch-box.md](docs/offline-branch-box.md) for the installation and outage drill.

## Verified

- The supermarket artwork is visible on the live TEST account picker and `/readyz` returned HTTP 200. [GitHub Actions passed for the reviewed commit](https://github.com/Isaac25-lgtm/SUPERMARKET/actions/runs/43824251310).
- On fresh loopback PostgreSQL databases, 121 backend tests and 19 standard browser tests passed. Ruff and the frontend production build passed. Windows PowerShell parsed all four package scripts.
- The standalone browser outage drill passed from source and again with `MotionBranchBox.exe` extracted from the ZIP. Both runs exercised a manager-approved weighed discount, an offline refund, automatic reconnection, and shift close. The ZIP's SHA-256 is `128b60945df3d60db0a1da55631f98f06c6d62dfe19f91704a519d261e279890`.

## Fix before the branch pilot

1. **Protect unsynced and rejected events during uninstall.** In [uninstall-box.ps1](branch-edge/windows/uninstall-box.ps1#L11), the pending-event check is skipped when the local status request fails, while `-RemoveData` can still delete the data directory. When the status request succeeds, the guard checks only `pending`, so it can also miss events awaiting review. Check the durable outbox for pending and rejected events before deleting its data. If the check is unavailable, preserve the data directory by default. Keep ordinary program removal and explicit forced data removal available. Verify the stopped-box, pending-event, rejected-event, and fully synced cases against disposable paths.
2. **Keep till trust stable across routine network changes.** [ensure_certificate](branch-edge/motion_edge/__main__.py#L63) can generate a new self-signed certificate when a LAN address changes, even when tills still use the same hostname. [setup-till.ps1](branch-edge/windows/setup-till.ps1#L36) trusts the specific old certificate on each till, so the replacement can interrupt every till after a reboot. For the pilot, use the hostname and reserve the back-office PC's LAN address. For deployment, give certificate renewal a stable trust anchor or an explicit rollover procedure. Check Windows trust and hostname validation after a reboot and an address change. The existing [Playwright outage drill](web/e2e/branch-box.spec.ts#L14) bypasses HTTPS certificate verification.

## Verify at one test branch

- Install under Windows with the real back-office and checkout accounts. Confirm startup after reboot, the actual shop network's Windows Firewall profile, certificate fingerprint verification and HTTPS trust, the desktop shortcut, and silent USB receipt printing.
- Run two tills during the same WAN outage. Include a weighed sale with a manager override, a refund, and reconnection while events are queued. Check each receipt and shift in head office against the local outbox and count once in stock, cash, and the ledger. Power-cycle the box on the UPS test setup and confirm the saved events survive and resume uploading.
- Demonstrate how a manager and head office identify and resolve a rejected event without changing the original record. Agree who owns cash from sales that upload after shift close. [CODEX_HANDOFF.md](CODEX_HANDOFF.md) tracks the broader workflow and device-bound offline credential work.

## Code maintenance

Keep central replay, edge storage, and the shared till UI independent. [Pos.tsx](web/src/pages/Pos.tsx) is the next focused cleanup candidate. Move the edge refund view and checkout payment UI into their own components once the browser cases cover retries and receipt recovery. Preserve the existing idempotency keys and shared UI entry points through the extraction.
