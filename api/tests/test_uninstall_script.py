"""uninstall-box.ps1 never deletes sales head office has not accepted (Windows, needs the built exe).

Runs the real script with the packaged MotionBranchBox.exe against disposable program/data folders
(-SkipSystem leaves the startup task, firewall and processes alone, so no Administrator is needed).
Build first: powershell -File branch-edge/windows/build.ps1
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "branch-edge"))
from motion_edge.store import Store  # noqa: E402

DIST = ROOT / "branch-edge" / "windows" / "dist" / "MotionBranchBox"
SCRIPT = ROOT / "branch-edge" / "windows" / "uninstall-box.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt" or not (DIST / "MotionBranchBox.exe").exists(),
                                reason="Windows with a built MotionBranchBox.exe only")


def _setup(tmp_path, statuses: list[str] | None, *, corrupt: bool = False, program: bool = True):
    prog, data = tmp_path / "Program", tmp_path / "Data"
    if program:
        shutil.copytree(DIST, prog)
    data.mkdir()
    if corrupt:
        (data / "box.sqlite3").write_bytes(b"not a database" * 100)
    elif statuses is not None:
        store = Store(data / "box.sqlite3")
        with store.write() as c:
            for i, status in enumerate(statuses):
                c.execute("""INSERT INTO outbox (event_id, created_at, register_id, cashier, local_receipt_no, payload, status, kind)
                             VALUES (?,?,?,?,?,?,?,'sale')""",
                          (str(uuid.uuid4()), dt.datetime.now(dt.timezone.utc).isoformat(), 1, "cash.x", f"T1-B{i:06d}",
                           json.dumps({"lines": []}), status))
        store._conn().close()
    return prog, data


def _run(prog, data, *flags) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in {"MOTION_EDGE_DB", "MOTION_EDGE_DATA"}}
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),  # noqa: S603, S607
                           "-ProgramDir", str(prog), "-DataDir", str(data), "-SkipSystem", *flags],
                          capture_output=True, text=True, env=env, timeout=180)


def test_pending_sales_block_removal(tmp_path):
    prog, data = _setup(tmp_path, ["acked", "pending"])
    r = _run(prog, data, "-RemoveData")
    assert r.returncode != 0 and "Nothing was removed" in (r.stdout + r.stderr)
    assert prog.exists() and (data / "box.sqlite3").exists()


def test_events_awaiting_review_keep_the_data_even_when_forced(tmp_path):
    prog, data = _setup(tmp_path, ["acked", "rejected"])
    r = _run(prog, data, "-Force", "-RemoveData")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not prog.exists() and (data / "box.sqlite3").exists()
    assert "KEPT" in r.stdout


def test_stopped_box_with_everything_accepted_removes_cleanly(tmp_path):
    prog, data = _setup(tmp_path, ["acked", "acked"])
    r = _run(prog, data, "-RemoveData")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not prog.exists() and not data.exists()


def test_unreadable_database_is_kept(tmp_path):
    prog, data = _setup(tmp_path, None, corrupt=True)
    assert _run(prog, data, "-RemoveData").returncode != 0
    r = _run(prog, data, "-Force", "-RemoveData")
    assert r.returncode == 0 and not prog.exists() and (data / "box.sqlite3").exists()


def test_missing_program_cannot_check_so_data_is_kept(tmp_path):
    prog, data = _setup(tmp_path, ["pending"], program=False)
    r = _run(prog, data, "-RemoveData")
    assert r.returncode != 0 and (data / "box.sqlite3").exists()


def test_explicit_force_remove_data_is_the_only_way_to_drop_unsent_sales(tmp_path):
    prog, data = _setup(tmp_path, ["pending"])
    r = _run(prog, data, "-Force", "-ForceRemoveData")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not prog.exists() and not data.exists()
