"""The branch box serving the till over the LAN, with head office unreachable, then reconnected.

Drives the box through the same HTTP API the till screen uses (cookie session + CSRF), and
checks that head office replays everything through its own rules: shift (cash session, blind
count, variance), sales on that shift, the in-person discount approval and the refund
(request -> manager approval -> pay-out), exactly once.
"""
from __future__ import annotations

import sys
import uuid
from decimal import Decimal

import pytest
from conftest import ROOT, on_hand, pid, q, q1
from fastapi.testclient import TestClient
from motion_api.creds import demo_password
from test_offline import FlakyCentral

sys.path.insert(0, str(ROOT / "branch-edge"))
from motion_edge import app as edge_app  # noqa: E402
from motion_edge import service  # noqa: E402
from motion_edge.store import Store  # noqa: E402

D = Decimal
CASHIER, MANAGER = "cash.sth1", "bm.moses"


@pytest.fixture(scope="module")
def box(login, ids, tmp_path_factory):
    # A clean start for the cashier at head office (an earlier test may have left a shift open).
    cashier = login(CASHIER)
    open_shift = cashier.get("/api/pos/shift").json()["shift"]
    if open_shift:
        assert cashier.post(f"/api/pos/shifts/{open_shift['id']}/close", {"counted_cash": "0"}).status_code == 200
    r = login("sys.admin").post("/api/sync/devices", {"branch_id": ids["branch"]["STH"], "name": "STH branch box (test)"})
    assert r.status_code == 201, r.text
    central = FlakyCentral(r.json()["token"])
    store = Store(tmp_path_factory.mktemp("box") / "box.sqlite3")
    app = edge_app.create_app(store=store, client=central, start_loop=False)
    syncer = app.state.syncer
    assert syncer.sync_once()["ok"] is True
    return {"app": app, "store": store, "syncer": syncer, "central": central}


def till(box) -> TestClient:
    return TestClient(box["app"], base_url="https://box.local")


def sign_in(client: TestClient, username: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": demo_password(username)})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": client.cookies.get("motion_csrf")}


def test_branch_box_till_day_offline_then_synced(box, ids, login):
    store, syncer, central = box["store"], box["syncer"], box["central"]
    sth = ids["storage"]["STH"]
    water, rice = pid(ids, "Drinking water 500ml"), pid(ids, "Rice - Super")
    stock0 = {p: on_hand(sth, p) for p in (water, rice)}
    register = ids["register"]["STH-T1"]

    c = till(box)
    assert c.get("/api/meta").json()["edge"] is True
    assert c.get("/api/auth/me").status_code == 401
    h = sign_in(c, CASHIER)
    me = c.get("/api/auth/me").json()
    assert me["username"] == CASHIER and "pos.sell" in me["permissions"]
    # CSRF is enforced on every change.
    # Zero float: any cash left in the till moves to the safe (a float top-up would drain the branch safe,
    # which later finance tests bank).
    assert c.post("/api/pos/shifts", json={"register_id": register, "opening_float": "0"}).status_code == 403

    central.down = True  # ---- the internet goes off for the whole shift
    syncer.sync_once()
    shift = c.post("/api/pos/shifts", json={"register_id": register, "opening_float": "0"}, headers=h).json()
    assert c.get("/api/pos/shift").json()["shift"]["id"] == shift["id"]
    cat = c.get(f"/api/pos/registers/{register}/catalog").json()
    price = {i["id"]: D(i["price"]) for i in cat["items"]}
    max_disc = D(str(cat["policy"]["cashier_max_discount_pct"]))

    # 1) Plain cash sale.
    s1 = c.post("/api/pos/sales", headers=h, json={
        "register_id": register, "idempotency_key": str(uuid.uuid4()),
        "lines": [{"product_id": water, "quantity": 3}], "payments": [{"method": "cash", "amount": 50000}]}).json()
    assert s1["number"].startswith("STH-T1-B") and D(s1["total"]) == 3 * price[water]

    # 2) A weighed item with a discount above the cashier limit: refused, then approved in person.
    big = max_disc + 5
    lines = [{"product_id": rice, "quantity": 1.5, "discount_pct": float(big)}]
    key = str(uuid.uuid4())
    refused = c.post("/api/pos/sales", headers=h, json={"register_id": register, "idempotency_key": key, "lines": lines,
                                                         "payments": [{"method": "cash", "amount": 100000}]})
    assert refused.status_code == 403
    bad = c.post("/api/pos/overrides", headers=h, json={"register_id": register, "manager_username": MANAGER,
                                                         "manager_password": "wrong-password", "reason": "Near expiry", "lines": lines})
    assert bad.status_code == 401
    ok = c.post("/api/pos/overrides", headers=h, json={"register_id": register, "manager_username": MANAGER,
                                                        "manager_password": demo_password(MANAGER), "reason": "Near expiry",
                                                        "lines": lines}).json()
    s2 = c.post("/api/pos/sales", headers=h, json={"register_id": register, "idempotency_key": key, "lines": lines,
                                                    "override_approval_id": ok["approval_id"],
                                                    "payments": [{"method": "cash", "amount": 100000}]}).json()
    assert D(s2["lines"][0]["discount_pct"]) == big
    # The approval is single use.
    again = c.post("/api/pos/sales", headers=h, json={"register_id": register, "idempotency_key": str(uuid.uuid4()),
                                                       "lines": lines, "override_approval_id": ok["approval_id"],
                                                       "payments": [{"method": "cash", "amount": 100000}]})
    assert again.status_code == 409

    # 3) Card and mobile money (references recorded, verified later by Finance).
    s3 = c.post("/api/pos/sales", headers=h, json={
        "register_id": register, "idempotency_key": str(uuid.uuid4()), "lines": [{"product_id": water, "quantity": 2}],
        "payments": [{"method": "card", "amount": float(price[water]), "reference": "CARD-77"},
                     {"method": "mobile_money", "amount": float(price[water]), "reference": "MM-88"}]}).json()
    assert {p["method"] for p in s3["payments"]} == {"card", "mobile_money"}

    # 4) Refund one water from sale 1, approved in person; the card/mobile-money receipt cannot be refunded in cash.
    found = c.get(f"/api/pos/receipts/{s1['number']}").json()
    rq = {"sale_id": found["id"], "lines": [{"line_id": 1, "quantity": "1"}], "reason": "Bottle was leaking",
          "manager_username": MANAGER, "manager_password": demo_password(MANAGER), "idempotency_key": str(uuid.uuid4())}
    refund = c.post("/api/pos/refunds/at-till", headers=h, json=rq).json()
    assert refund["kind"] == "refund" and D(refund["total"]) == -price[water]
    assert c.post("/api/pos/refunds/at-till", headers=h, json=rq).json()["id"] == refund["id"]  # replay, not twice
    no_cash = c.post("/api/pos/refunds/at-till", headers=h, json={
        **rq, "sale_id": c.get(f"/api/pos/receipts/{s3['number']}").json()["id"], "idempotency_key": str(uuid.uuid4())})
    assert no_cash.status_code == 409
    assert D(c.get(f"/api/pos/receipts/{s1['number']}").json()["lines"][0]["refunded_qty"]) == 1

    # 5) Close the shift with a blind count: zero float + cash sales - the refund.
    cash_in = 3 * price[water] + D(s2["total"])
    expected = cash_in - price[water]
    closed = c.post(f"/api/pos/shifts/{shift['id']}/close", headers=h,
                    json={"counted_cash": str(expected - 500), "cash_drop": "0"}).json()
    assert D(closed["variance"]) == -500
    assert service.status(store)["pending"] == 6  # open, 3 sales, refund, close

    # ---- the internet comes back: everything goes to head office, in order, once.
    central.down = False
    assert syncer.sync_once()["ok"] is True
    st = service.status(store)
    assert st["pending"] == 0 and st["rejected"] == 0, store._conn().execute(
        "SELECT kind, last_error, ack FROM outbox WHERE status<>'acked'").fetchall()
    head = q1("""SELECT s.* FROM motion.shift s JOIN motion.app_user u ON u.id=s.cashier_id
                 WHERE u.username=%s ORDER BY s.id DESC LIMIT 1""", (CASHIER,))
    assert head["status"] == "closed" and head["register_id"] == register
    assert D(head["expected_cash"]) == expected and D(head["variance"]) == -500
    sales = q("SELECT kind, total, approval_id FROM motion.pos_sale WHERE shift_id=%s ORDER BY id", (head["id"],))
    assert [s["kind"] for s in sales] == ["sale", "sale", "sale", "refund"]
    assert sales[3]["approval_id"] is not None and D(sales[3]["total"]) == -price[water]
    approval = q1("SELECT status, consumed_at FROM motion.approval_request WHERE id=%s", (sales[3]["approval_id"],))
    assert approval["status"] in {"executed", "approved"} and approval["consumed_at"] is not None
    assert on_hand(sth, water) == stock0[water] - 5 + 1  # 3 + 2 sold, 1 returned
    assert on_hand(sth, rice) == stock0[rice] - D("1.5")
    # The in-person discount approval is accepted at head office (no policy conflict).
    conflicts = q("""SELECT detail FROM motion.discrepancy WHERE kind='sync_conflict' AND source_type='pos_sale'
                     AND source_id IN (SELECT id::text FROM motion.pos_sale WHERE shift_id=%s)""", (head["id"],))
    assert not [x for x in conflicts if x["detail"].get("type") in {"discount_policy_divergence", "override_not_valid_at_sync"}]
    # A resend after a lost acknowledgement changes nothing.
    import json
    rows = store._conn().execute("SELECT event_id, kind, payload FROM outbox ORDER BY created_at").fetchall()
    dup = central.post("/api/edge/events", json={"events": [
        {"event_id": r["event_id"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in rows]}).json()
    assert all(a.get("duplicate") for a in dup["acks"])
    assert q1("SELECT count(*) AS n FROM motion.pos_sale WHERE shift_id=%s", (head["id"],))["n"] == 4
    assert q1("SELECT count(*) AS n FROM (SELECT move FROM account_move_line GROUP BY move HAVING sum(debit)<>sum(credit)) x")["n"] == 0
    # The receipt now shows the head-office number.
    assert c.get(f"/api/pos/receipts/{s1['number']}").json()["central_number"]


def test_branch_box_rules_at_the_till(box, ids):
    c = till(box)
    h = sign_in(c, MANAGER)
    me = c.get("/api/auth/me").json()
    assert "pos.sell" not in me["permissions"]  # managers approve on the cashier's till
    r = c.post("/api/pos/shifts", headers=h, json={"register_id": ids["register"]["STH-T1"], "opening_float": "0"})
    assert r.status_code == 403
    assert c.get("/api/catalog/lookup", params={"code": "0000000000000"}).status_code == 404
    assert c.get("/api/finance/pnl").status_code == 404  # back-office pages are not served by the box
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_branch_box_stops_selling_after_a_day_offline(box, ids, monkeypatch):
    store = box["store"]
    with store.write() as conn:
        store.set_meta(conn, "snapshot_at", "2000-01-01T00:00:00+00:00")
    try:
        c = till(box)
        h = sign_in(c, CASHIER)
        r = c.post("/api/pos/shifts", headers=h, json={"register_id": ids["register"]["STH-T1"], "opening_float": "0"})
        assert r.status_code == 503 and "offline for more than" in r.json()["message"]
    finally:
        assert box["syncer"].sync_once()["ok"] is True  # fresh snapshot again


def test_shift_events_wait_for_their_dependencies(box, login):
    """A sale that arrives before its shift opening is retried, not rejected."""
    central = box["central"]
    early = central.post("/api/edge/events", json={"events": [{
        "event_id": str(uuid.uuid4()), "kind": "shift_close", "payload": {
            "shift_event_id": str(uuid.uuid4()), "register_id": 1, "cashier_username": CASHIER, "counted_cash": "0",
            "local_created_at": "2026-11-16T08:00:00+03:00"}}]}).json()
    assert early["acks"][0]["status"] == "retry"


def test_box_will_not_open_a_till_that_has_an_online_shift(box, ids, login):
    """A till left open at head office (e.g. used online while the box was off) cannot get a second shift."""
    other = login("cash.sth1")
    reg = ids["register"]["STH-T1"]
    opened = other.post("/api/pos/shifts", {"register_id": reg, "opening_float": "0"})
    assert opened.status_code in (200, 201), opened.text
    try:
        assert box["syncer"].sync_once()["ok"] is True  # the snapshot now lists the open till
        c = till(box)
        h = sign_in(c, "cash.sth1")
        same = c.post("/api/pos/shifts", headers=h, json={"register_id": reg, "opening_float": "0"})
        assert same.status_code in (200, 409)  # the same cashier may continue their own till
        if same.status_code == 200:
            sid = same.json()["id"]
            c.post(f"/api/pos/shifts/{sid}/close", headers=h, json={"counted_cash": "0"})
        store = box["store"]
        with store.write() as conn:
            store.set_meta(conn, "open_shifts", [{"register_id": reg, "username": "cash.nth1", "full_name": "Isaac Opio (TEST)"}])
        blocked = c.post("/api/pos/shifts", headers=h, json={"register_id": reg, "opening_float": "0"})
        assert blocked.status_code == 409 and "still has Isaac Opio's shift open" in blocked.json()["message"]
    finally:
        shift = other.get("/api/pos/shift").json()["shift"]
        if shift:
            other.post(f"/api/pos/shifts/{shift['id']}/close", {"counted_cash": "0"})
        box["syncer"].sync_once()
