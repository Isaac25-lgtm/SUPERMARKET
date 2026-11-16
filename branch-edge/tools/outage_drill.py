"""End-to-end outage drill on one machine: head office (local dev server) + a real branch box + browser.

    python branch-edge/tools/outage_drill.py --creds e2e-creds.json [--central http://127.0.0.1:8000]

1. registers a branch box for the Central branch at head office (as the technical admin),
2. starts the internet relay and the branch box as its own HTTPS program (``python -m motion_edge``),
3. runs ``web/e2e/branch-box.spec.ts``, which sells through the box, cuts the internet,
   keeps selling (discount approval, refund, shift close), reconnects and checks head office,
4. stops everything. Run against a disposable development database only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def central_session(base: str, user: str, password: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30)
    r = c.post("/api/auth/login", json={"username": user, "password": password})
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = c.cookies.get("motion_csrf")
    return c


def stop(proc: subprocess.Popen) -> None:
    """End a helper and its children (a venv's python.exe on Windows is a launcher with a child)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)  # noqa: S603, S607
    else:
        proc.terminate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", required=True)
    ap.add_argument("--central", default="http://127.0.0.1:8000")
    ap.add_argument("--branch", default="CEN")
    ap.add_argument("--cashier", default="cash.cen2")
    ap.add_argument("--keep", action="store_true", help="leave the box running afterwards")
    ap.add_argument("--box-exe", help="run the packaged MotionBranchBox.exe instead of python -m motion_edge")
    args = ap.parse_args()
    host = httpx.URL(args.central).host
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("The drill writes test sales: point it at a local development server only.")
    creds = json.loads(Path(args.creds).read_text(encoding="utf-8"))

    owner = central_session(args.central, "hm.owner", creds["hm.owner"])
    branch = next(b for b in owner.get("/api/auth/me").json()["branches"] if b["code"] == args.branch)
    cashier = central_session(args.central, args.cashier, creds[args.cashier])
    shift = cashier.get("/api/pos/shift").json()["shift"]
    if shift:  # start clean: the drill opens its own shift through the box
        cashier.post(f"/api/pos/shifts/{shift['id']}/close", json={"counted_cash": "0", "note": "drill reset"}).raise_for_status()
    admin = central_session(args.central, "sys.admin", creds["sys.admin"])
    me = admin.get("/api/auth/me").json()
    if not me["all_branches"] and branch["id"] not in me["branch_ids"]:
        # Seeded demo data gives the technical admin all branches; restore it on this dev database.
        print("granting sys.admin all-branch scope for device registration (dev database)", flush=True)
        owner.put(f"/api/admin/users/{me['id']}/access", json={
            "roles": me["roles"], "branch_ids": [], "all_branches": True,
            "reason": "Outage drill: device registration needs branch scope"}).raise_for_status()
        admin = central_session(args.central, "sys.admin", creds["sys.admin"])
    dev = admin.post("/api/sync/devices", json={"branch_id": branch["id"], "name": f"{args.branch} drill box"})
    dev.raise_for_status()

    work = Path(tempfile.mkdtemp(prefix="motion-drill-"))
    flag = work / "wan-down.flag"
    data = work / "box"
    data.mkdir()
    (data / "config.json").write_text(json.dumps({
        "central_url": "http://127.0.0.1:8099", "device_token": dev.json()["token"],
        "https_port": 8443, "http_port": 8080, "bind": "127.0.0.1"}), encoding="utf-8")
    env = {**os.environ, "MOTION_EDGE_DATA": str(data), "MOTION_EDGE_ALLOW_INSECURE_CENTRAL": "1"}
    if not args.box_exe:  # the packaged exe uses the till screen shipped next to it
        env.update({"MOTION_EDGE_WEB_DIR": str(ROOT / "web" / "dist"), "PYTHONPATH": str(ROOT / "branch-edge")})
    # Fixed local programs only; the target host was checked to be this machine above.
    relay = subprocess.Popen([sys.executable, str(ROOT / "branch-edge/tools/wan_relay.py"), "--flag", str(flag),  # noqa: S603
                              "--target", f"{host}:{httpx.URL(args.central).port or 80}"])
    command = [str(Path(args.box_exe).resolve())] if args.box_exe else [sys.executable, "-m", "motion_edge"]
    box = subprocess.Popen(command, env=env, cwd=str(work))  # noqa: S603
    try:
        for _ in range(60):
            try:
                st = httpx.get("https://127.0.0.1:8443/edge/status", verify=False, timeout=3).json()  # noqa: S501
                if st.get("snapshot_at"):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise SystemExit("The branch box did not finish its first update from head office.")
        print("box ready; fingerprint", (data / "box-fingerprint.txt").read_text(encoding="utf-8"), flush=True)
        e2e_env = {**os.environ, "E2E_BOX_URL": "https://127.0.0.1:8443", "E2E_WAN_FLAG": str(flag),
                   "E2E_CREDS": str(Path(args.creds).resolve()), "E2E_BOX_CASHIER": args.cashier,
                   "E2E_CENTRAL_URL": args.central}
        npx = "npx.cmd" if os.name == "nt" else "npx"
        return subprocess.call([npx, "playwright", "test", "-c", "e2e/playwright.config.ts", "branch-box"],  # noqa: S603
                               cwd=str(ROOT / "web"), env=e2e_env)
    finally:
        if not args.keep:
            stop(box)
            stop(relay)


if __name__ == "__main__":
    raise SystemExit(main())
