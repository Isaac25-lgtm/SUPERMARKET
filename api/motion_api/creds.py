"""Deterministic demo/UAT credentials derived from a secret.

Seeded test accounts get passwords = f(MOTION_SEED_PASSWORD_SECRET, username),
so no password is ever committed or logged.  Whoever holds the secret (the
developer, via the git-ignored env file) can print them locally:

    MOTION_ENV_FILE=.env.dev python -m motion_api.creds
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys

from .settings import _load_env_file

DEMO_USERS = [
    # username, full name, roles, branch codes ("*" = all branches)
    ("hm.owner", "Helen Mukasa (TEST Head Manager/CFO)", ["owner"], "*"),
    ("gm.grace", "Grace Atim (TEST General Manager)", ["general_manager"], "*"),
    ("acc.amina", "Amina Nalwoga (TEST Accountant)", ["accountant"], "*"),
    ("ops.david", "David Okello (TEST Operations Manager)", ["operations_manager"], "*"),
    ("inv.ruth", "Ruth Kyomuhendo (TEST Inventory Manager)", ["inventory_manager"], ["CEN", "NTH", "STH"]),
    ("bm.joseph", "Joseph Ssali (TEST Branch Manager Central)", ["branch_manager"], ["CEN"]),
    ("bm.sarah", "Sarah Achieng (TEST Branch Manager North)", ["branch_manager"], ["NTH"]),
    ("bm.moses", "Moses Tumusiime (TEST Branch Manager South)", ["branch_manager"], ["STH"]),
    ("cash.cen1", "Brian Kato (TEST Cashier CEN-T1)", ["cashier"], ["CEN"]),
    ("cash.cen2", "Esther Namubiru (TEST Cashier CEN-T2)", ["cashier"], ["CEN"]),
    ("cash.nth1", "Isaac Opio (TEST Cashier NTH-T1)", ["cashier"], ["NTH"]),
    ("cash.nth2", "Mary Akello (TEST Cashier NTH-T2)", ["cashier"], ["NTH"]),
    ("cash.sth1", "Peter Mugisha (TEST Cashier STH-T1)", ["cashier"], ["STH"]),
    ("sys.admin", "TEST Technical Administrator", ["sysadmin"], []),
]


def demo_password(username: str, secret: str | None = None) -> str:
    secret = secret or os.environ.get("MOTION_SEED_PASSWORD_SECRET")
    if not secret or len(secret) < 16:
        raise RuntimeError("MOTION_SEED_PASSWORD_SECRET (>=16 chars) is required to seed demo users")
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha256).digest()
    token = base64.b32encode(digest).decode().rstrip("=").lower()
    return f"{token[:5]}-{token[5:10]}-{(digest[0] % 90) + 10}"


if __name__ == "__main__":
    if os.environ.get("MOTION_ENV_FILE"):
        from pathlib import Path

        _load_env_file(Path(os.environ["MOTION_ENV_FILE"]))
    sys.stdout.reconfigure(encoding="utf-8")
    print("TEST/UAT accounts (do not share outside the test team):")
    for username, name, roles, _ in DEMO_USERS:
        print(f"  {username:<11} {demo_password(username):<16} {', '.join(roles):<20} {name}")
