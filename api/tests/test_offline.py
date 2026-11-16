"""Branch-edge offline operation against the real central API (WAN outage simulated)."""
from __future__ import annotations

import sys
import threading
import uuid
from decimal import Decimal

import httpx
import pytest
from conftest import ROOT, app, on_hand, pid, q, q1
from fastapi.testclient import TestClient
from motion_api.creds import demo_password

sys.path.insert(0, str(ROOT / "branch-edge"))
from motion_edge import service  # noqa: E402
from motion_edge.store import Store  # noqa: E402

D = Decimal


class FlakyCentral(httpx.Client):
    """Real central app over an in-process transport that can be 'unplugged'."""

    def __init__(self, token: str):
        self.down = False
        self._tc = TestClient(app)
        super().__init__(base_url="http://central", headers={"Authorization": f"Bearer {token}"},
                         transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("simulated WAN outage", request=request)
        r = self._tc.request(request.method, request.url.path, content=request.content,
                             headers={k: v for k, v in request.headers.items() if k.lower() in {"authorization", "content-type"}},
                             params=dict(request.url.params))
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": r.headers.get("content-type", "")})


@pytest.fixture(scope="module")
def edge(login, ids, tmp_path_factory):
    admin = login("sys.admin")
    r = admin.post("/api/sync/devices", {"branch_id": ids["branch"]["NTH"], "name": "NTH test edge"})
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    path = tmp_path_factory.mktemp("edge") / "edge.sqlite3"
    central = FlakyCentral(token)
    store = Store(path)
    syncer = service.Syncer(store, central)
    assert syncer.sync_once()["ok"] is True
    return {"store": store, "syncer": syncer, "central": central, "path": path, "token": token, "device_id": r.json()["id"]}


def _session(store, username, register_id):
    s = service.login(store, username, demo_password(username), register_id)
    return service.session(store, s["token"])


def test_offline_sales_sync_exactly_once(edge, ids, login):
    store, syncer, central = edge["store"], edge["syncer"], edge["central"]
    regs = [ids["register"]["NTH-T1"], ids["register"]["NTH-T2"]]
    sess = [_session(store, "cash.nth1", regs[0]), _session(store, "bm.sarah", regs[1])]
    soda, water, rice = pid(ids, "Soda 500ml"), pid(ids, "Drinking water 500ml"), pid(ids, "Rice - Super")
    nth = ids["storage"]["NTH"]
    stock0 = {p: on_hand(nth, p) for p in (soda, water, rice)}
    central_before = q1("SELECT count(*) AS n FROM motion.pos_sale WHERE source='edge'")["n"]

    central.down = True  # ---- WAN outage
    assert syncer.sync_once()["ok"] is False
    sold = {soda: D(0), water: D(0), rice: D(0)}
    events = []
    for i in range(35):
        s = sess[i % 2]
        lines = [{"product_id": water, "quantity": "1"}]
        if i % 3 == 0:
            lines.append({"product_id": soda, "quantity": "2"})
        if i % 5 == 0:
            lines.append({"product_id": rice, "quantity": "0.75"})
        for ln in lines:
            sold[ln["product_id"]] += D(ln["quantity"])
        pays = [{"method": "cash", "amount": "50000"}] if i % 7 else [{"method": "mobile_money", "amount": "999999", "reference": f"MM{i}"}]
        if i % 7 == 0:  # exact mobile-money claim for the computed total
            items = {it["id"]: D(it["price"]) for it in [__import__("json").loads(r["data"]) for r in store._conn().execute("SELECT data FROM items")]}
            total = sum(((D(ln["quantity"]) * items[ln["product_id"]]).quantize(D("1")) for ln in lines), D(0))
            pays = [{"method": "mobile_money", "amount": str(total), "reference": f"MM{i}"}]
        r = service.sell(store, s, client_key=str(uuid.uuid4()), lines=lines, payments=pays)
        assert r["status"] == "pending" and "-OFF-" in r["local_receipt_no"]
        events.append(r["event_id"])
    # Central price change during the outage -> offline sales carry the old price.
    pr = login("inv.ruth").post(f"/api/catalog/products/{soda}/price", {"new_price": "2100", "reason": "Outage price-change test"})
    assert pr.status_code == 200, pr.text
    # Durable across an edge restart (new process / connection on the same file).
    restarted = Store(edge["path"])
    assert service.status(restarted)["pending"] == 35
    assert service.status(restarted)["state"] == "offline"

    central.down = False  # ---- reconnect
    res = syncer.sync_once()
    assert res["ok"] is True and res["pushed"] == 35
    st = service.status(store)
    assert st["pending"] == 0 and st["acked"] >= 35
    assert q1("SELECT count(*) AS n FROM motion.pos_sale WHERE source='edge'")["n"] == central_before + 35
    for p, qty in sold.items():
        assert on_hand(nth, p) == stock0[p] - qty, p
    # Attribution preserved: original cashier and local timestamp.
    by = {r["username"] for r in q("""SELECT u.username FROM motion.pos_sale s JOIN motion.app_user u ON u.id=s.cashier_id
                                       WHERE s.source='edge'""")}
    assert {"cash.nth1", "bm.sarah"} <= by
    assert q1("SELECT count(*) AS n FROM motion.pos_sale WHERE source='edge' AND local_created_at IS NULL")["n"] == 0
    # Price divergence flagged for review, not silently changed.
    conflicts = q("SELECT detail FROM motion.discrepancy WHERE kind='sync_conflict'")
    assert any(c["detail"].get("type") == "price_version_divergence" for c in conflicts)
    # Unverified mobile-money claims stay flagged.
    assert q1("SELECT count(*) AS n FROM motion.pos_sale WHERE source='edge' AND unverified_claims")["n"] >= 5
    # Duplicate resend (e.g. lost acknowledgement) posts nothing new.
    rows = [dict(r) for r in store._conn().execute("SELECT event_id, payload FROM outbox LIMIT 5")]
    import json
    dup = central.post("/api/edge/events", json={"events": [{"event_id": r["event_id"], "kind": "sale", "payload": json.loads(r["payload"])} for r in rows]}).json()
    assert all(a["duplicate"] for a in dup["acks"])
    assert q1("SELECT count(*) AS n FROM motion.pos_sale WHERE source='edge'")["n"] == central_before + 35
    # Ledger still balanced after bulk ingest.
    assert q1("SELECT count(*) AS n FROM (SELECT move FROM account_move_line GROUP BY move HAVING sum(debit)<>sum(credit)) x")["n"] == 0


def test_edge_serialises_last_unit_between_tills(edge, ids):
    store, central = edge["store"], edge["central"]
    regs = [ids["register"]["NTH-T1"], ids["register"]["NTH-T2"]]
    sess = [_session(store, "cash.nth1", regs[0]), _session(store, "bm.sarah", regs[1])]
    product = pid(ids, "Butter")
    with store.connect() as c:
        have = service.available(store, c, product)
    central.down = True
    if have > 1:
        service.sell(store, sess[0], client_key=str(uuid.uuid4()), lines=[{"product_id": product, "quantity": str(int(have) - 1)}],
                     payments=[{"method": "cash", "amount": "9999999"}])
    results = []
    barrier = threading.Barrier(2)

    def go(s):
        barrier.wait()
        try:
            service.sell(store, s, client_key=str(uuid.uuid4()), lines=[{"product_id": product, "quantity": "1"}],
                         payments=[{"method": "cash", "amount": "9000"}])
            results.append("ok")
        except service.EdgeError as e:
            results.append(e.status)
    ts = [threading.Thread(target=go, args=(s,)) for s in sess]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(map(str, results)) == ["409", "ok"], results
    central.down = False
    assert edge["syncer"].sync_once()["ok"]


def test_suspended_user_loses_offline_access_after_sync(edge, ids, login):
    store = edge["store"]
    uid = ids["user"]["cash.nth2"]
    login("hm.owner").post(f"/api/admin/users/{uid}/suspend", {"reason": "offline revocation test"})
    assert edge["syncer"].sync_once()["ok"]
    with pytest.raises(service.EdgeError):
        service.login(store, "cash.nth2", demo_password("cash.nth2"), ids["register"]["NTH-T1"])
    login("hm.owner").post(f"/api/admin/users/{uid}/reactivate", {"reason": "restore"})


def test_stock_stays_reserved_until_central_snapshot(edge, ids):
    store, syncer, central = edge["store"], edge["syncer"], edge["central"]
    session = _session(store, "cash.nth1", ids["register"]["NTH-T1"])
    product = pid(ids, "Drinking water 500ml")
    with store.connect() as conn:
        before = service.available(store, conn, product)
    service.sell(store, session, client_key=str(uuid.uuid4()), lines=[{"product_id": product, "quantity": "1"}],
                 payments=[{"method": "cash", "amount": "50000"}])
    assert syncer.push()["sent"] == 1
    central.down = True
    try:
        assert not syncer.sync_once()["ok"]
        with store.connect() as conn:
            assert service.available(store, conn, product) == before - 1
    finally:
        central.down = False
    assert syncer.sync_once()["ok"]
    with store.connect() as conn:
        assert service.available(store, conn, product) == before - 1


def test_missing_offline_ack_is_retryable(edge, ids):
    store = edge["store"]
    session = _session(store, "cash.nth1", ids["register"]["NTH-T1"])
    event_id = str(uuid.uuid4())
    service.sell(store, session, client_key=event_id,
                 lines=[{"product_id": pid(ids, "Drinking water 500ml"), "quantity": "1"}],
                 payments=[{"method": "cash", "amount": "50000"}])
    calls = []

    def no_ack(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"acks": []})

    with httpx.Client(base_url="http://local-test", transport=httpx.MockTransport(no_ack)) as client:
        assert not service.Syncer(store, client).sync_once()["ok"]
    assert calls == ["/api/edge/events"]
    with store.connect() as conn:
        row = conn.execute("SELECT status, attempts FROM outbox WHERE event_id=?", (event_id,)).fetchone()
        assert (row["status"], row["attempts"]) == ("pending", 1)
    assert edge["syncer"].sync_once()["ok"]


def test_outbox_is_immutable(edge):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        edge["store"]._conn().execute("UPDATE outbox SET payload='{}'")
    with pytest.raises(sqlite3.IntegrityError):
        edge["store"]._conn().execute("DELETE FROM outbox")


def test_revoked_device_cannot_sync(edge, login):
    admin = login("sys.admin")
    assert admin.post(f"/api/sync/devices/{edge['device_id']}/revoke", {"reason": "lost device test"}).status_code == 200
    r = edge["central"].get("/api/edge/snapshot")
    assert r.status_code == 401
