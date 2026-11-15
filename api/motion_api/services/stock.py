"""Stock: levels & valuation, movements, inter-branch transfers, discrepancies,
write-offs and counts.

Transfers use Tryton ``stock.shipment.internal`` between branch warehouses.
Because the warehouses differ, Tryton routes stock through the transit
location: dispatch moves origin storage -> transit (origin stock falls,
consolidated stock unchanged), receipt moves transit -> destination storage.
Internal moves create no revenue and no journal entries (continental
valuation only books moves that leave/enter the company's storage), so
transfers cannot inflate sales or profit.  Units dispatched but not received
remain visibly in transit as an open discrepancy until an authorised
write-off (transit -> lost & found, expensed as shrinkage) or a late receipt.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from .. import tryton as t
from ..audit import record
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..security import Principal
from . import approvals, catalog, common


def _cost(product_ids: list[int]) -> dict[int, Decimal]:
    return {r["id"]: r["cost_price"] for r in catalog._rows(ids=product_ids, active=None)}


def _uoms(product_ids: list[int]) -> dict[int, int]:
    return {r["id"]: r["default_uom"] for r in t.sql(
        "SELECT pp.id, pt.default_uom FROM product_product pp JOIN product_template pt ON pt.id=pp.template WHERE pp.id = ANY(%s)",
        (product_ids,))}


def overview(actor: Principal, branch_id: int | None = None) -> dict:
    actor.require("stock.view")
    scope = [b for b in common.branches() if actor.can_branch(b["id"]) and (branch_id is None or b["id"] == branch_id)]
    if branch_id is not None and not scope:
        actor.require_branch(branch_id, action="stock.view")
    locs = [b["storage_location_id"] for b in scope]
    on_hand = common.stock_on_hand(locs)
    transit = common.stock_on_hand([common.transit_location_id()])
    costs = {r["id"]: r["cost_price"] for r in catalog._rows(active=None)}
    show_value = actor.has("finance.view") or actor.has("catalog.manage")
    per_branch = []
    for b in scope:
        qty_lines = {pid: q for (loc, pid), q in on_hand.items() if loc == b["storage_location_id"]}
        value = sum((q * costs.get(pid, Decimal(0)) for pid, q in qty_lines.items() if q > 0), Decimal(0))
        negative = sum(1 for q in qty_lines.values() if q < 0)
        per_branch.append({"branch_id": b["id"], "code": b["code"], "name": b["name"], "sku_count": sum(1 for q in qty_lines.values() if q > 0),
                           "value": common.money(value) if show_value else None, "negative_lines": negative})
    in_transit_value = sum((q * costs.get(pid, Decimal(0)) for (_, pid), q in transit.items() if q > 0), Decimal(0))
    transfers_in_transit = t.sql("""SELECT count(*) AS n FROM motion.transfer WHERE status='dispatched'
                                    AND (from_branch_id = ANY(%s) OR to_branch_id = ANY(%s))""",
                                 ([b["id"] for b in scope], [b["id"] for b in scope]))[0]["n"]
    return {"branches": per_branch, "in_transit_value": common.money(in_transit_value) if show_value else None,
            "transfers_in_transit": transfers_in_transit,
            "open_discrepancies": discrepancies(actor, status="open", branch_id=branch_id)[:20]}


def movements(actor: Principal, *, branch_id: int, product_id: int | None = None, days: int = 30) -> list[dict]:
    actor.require("stock.view")
    actor.require_branch(branch_id, action="stock.movements")
    b = common.branch(branch_id)
    since = common.today() - dt.timedelta(days=days)
    rows = t.sql("""
        SELECT m.id, m.effective_date, m.product, pt.name AS product_name, m.internal_quantity AS qty,
               fl.name AS from_name, fl.type AS from_type, tl.name AS to_name, tl.type AS to_type,
               m.origin, m.shipment, m.state, cu.name AS created_by
        FROM stock_move m JOIN product_product pp ON pp.id=m.product JOIN product_template pt ON pt.id=pp.template
        JOIN stock_location fl ON fl.id=m.from_location JOIN stock_location tl ON tl.id=m.to_location
        LEFT JOIN res_user cu ON cu.id=m.create_uid
        WHERE m.state='done' AND (m.from_location=%s OR m.to_location=%s) AND m.effective_date >= %s
          AND (%s::int IS NULL OR m.product=%s)
        ORDER BY m.effective_date DESC, m.id DESC LIMIT 300""",
        (b["storage_location_id"], b["storage_location_id"], since, product_id, product_id))
    for r in rows:
        r["direction"] = "in" if r["to_name"].startswith(b["code"]) else "out"
        r["kind"] = (
            "sale" if (r["origin"] or "").startswith("sale.point.sale.line") and r["to_type"] == "customer" else
            "refund" if (r["origin"] or "").startswith("sale.point.sale.line") else
            "transfer" if r["shipment"] and r["shipment"].startswith("stock.shipment.internal") else
            "receipt" if r["from_type"] == "supplier" or (r["shipment"] or "").startswith("stock.shipment.in") else
            "adjustment" if "lost_found" in (r["from_type"], r["to_type"]) else "other")
    return rows


# ------------------------------------------------------------------ transfers

def _transfer(transfer_id: int, lock: bool = False) -> dict:
    row = t.sql_one("SELECT * FROM motion.transfer WHERE id=%s" + (" FOR UPDATE" if lock else ""), (transfer_id,))
    if not row:
        raise NotFound("Transfer not found.")
    return row


def _transfer_view(row: dict) -> dict:
    row = dict(row)
    row["lines"] = t.sql("""SELECT l.*, pt.name AS product_name, u.symbol AS uom FROM motion.transfer_line l
                            JOIN product_product pp ON pp.id=l.product_id JOIN product_template pt ON pt.id=pp.template
                            JOIN product_uom u ON u.id=pt.default_uom WHERE l.transfer_id=%s ORDER BY l.id""", (row["id"],))
    names = common.user_brief({row["requested_by"], row["dispatched_by"], row["received_by"]})
    row["requested_by_name"] = names.get(row["requested_by"])
    row["dispatched_by_name"] = names.get(row["dispatched_by"])
    row["received_by_name"] = names.get(row["received_by"])
    bmap = {b["id"]: b for b in common.branches()}
    row["from_branch"] = bmap[row["from_branch_id"]]["name"]
    row["to_branch"] = bmap[row["to_branch_id"]]["name"]
    if row["approval_id"]:
        a = t.sql_one("SELECT ref, status, current_step, steps FROM motion.approval_request WHERE id=%s", (row["approval_id"],))
        row["approval"] = {"ref": a["ref"], "status": a["status"]}
    row["discrepancies"] = t.sql("""SELECT * FROM motion.discrepancy WHERE source_type='transfer' AND source_id=%s
                                    ORDER BY id""", (str(row["id"]),))
    return row


def list_transfers(actor: Principal, status: str | None = None) -> list[dict]:
    actor.require("stock.view")
    rows = t.sql("""SELECT * FROM motion.transfer WHERE (%s::text IS NULL OR status=%s) ORDER BY requested_at DESC LIMIT 200""",
                 (status, status))
    return [_transfer_view(r) for r in rows if actor.can_branch(r["from_branch_id"]) or actor.can_branch(r["to_branch_id"])]


def get_transfer(actor: Principal, transfer_id: int) -> dict:
    actor.require("stock.view")
    row = _transfer(transfer_id)
    if not (actor.can_branch(row["from_branch_id"]) or actor.can_branch(row["to_branch_id"])):
        raise Forbidden("You are not assigned to either branch of this transfer.", action="transfer.view",
                        target_type="transfer", target_id=row["ref"])
    return _transfer_view(row)


def request_transfer(actor: Principal, *, from_branch_id: int, to_branch_id: int, lines: list[dict], reason: str) -> dict:
    actor.require("transfer.request", action="transfer.request")
    if from_branch_id == to_branch_id:
        raise Invalid("Choose two different branches.")
    if not (actor.can_branch(to_branch_id) or actor.can_branch(from_branch_id)):
        actor.require_branch(to_branch_id, action="transfer.request")
    common.branch(from_branch_id), common.branch(to_branch_id)
    if not lines:
        raise Invalid("Add at least one product.")
    pids = [int(ln["product_id"]) for ln in lines]
    if len(set(pids)) != len(pids):
        raise Invalid("List each product once.")
    costs = _cost(pids)
    amount = Decimal(0)
    for ln in lines:
        q = common.qty(ln["quantity"])
        if q <= 0:
            raise Invalid("Quantities must be positive.")
        if int(ln["product_id"]) not in costs:
            raise Invalid("Unknown product.")
        amount += q * costs[int(ln["product_id"])]
    ref = common.next_ref("TRF", "transfer_ref_seq")
    row = t.sql_one("""INSERT INTO motion.transfer (ref, from_branch_id, to_branch_id, status, reason, requested_by)
                       VALUES (%s,%s,%s,'requested',%s,%s) RETURNING *""",
                    (ref, from_branch_id, to_branch_id, reason, actor.user_id))
    for ln in lines:
        t.execute("INSERT INTO motion.transfer_line (transfer_id, product_id, requested_qty) VALUES (%s,%s,%s)",
                  (row["id"], int(ln["product_id"]), common.qty(ln["quantity"])))
    names = common.product_names(set(pids))
    # The request is filed under the branch the requester belongs to (normally the receiving branch).
    approval_branch = to_branch_id if actor.can_branch(to_branch_id) else from_branch_id
    approval = approvals.submit(actor, action_type="transfer", branch_id=approval_branch, amount=common.money(amount),
                                title=f"{ref}: {common.branch(from_branch_id)['code']} -> {common.branch(to_branch_id)['code']}",
                                payload={"transfer_id": row["id"], "ref": ref,
                                         "lines": [{"product": names[int(x['product_id'])]["name"], "quantity": str(x["quantity"])} for x in lines]},
                                justification=reason, audit_action="transfer.request")
    t.execute("UPDATE motion.transfer SET approval_id=%s WHERE id=%s", (approval["id"], row["id"]))
    return get_transfer(actor, row["id"])


def sync_transfer_approval(transfer_id: int) -> None:
    row = _transfer(transfer_id, lock=True)
    if row["status"] != "requested" or not row["approval_id"]:
        return
    a = t.sql_one("SELECT status FROM motion.approval_request WHERE id=%s", (row["approval_id"],))
    if a["status"] == "approved":
        t.execute("UPDATE motion.transfer SET status='approved' WHERE id=%s", (transfer_id,))
    elif a["status"] in {"rejected", "cancelled"}:
        t.execute("UPDATE motion.transfer SET status='rejected' WHERE id=%s", (transfer_id,))


def dispatch(actor: Principal, transfer_id: int, *, lines: list[dict] | None = None) -> dict:
    actor.require("transfer.dispatch", action="transfer.dispatch")
    sync_transfer_approval(transfer_id)
    row = _transfer(transfer_id, lock=True)
    actor.require_branch(row["from_branch_id"], action="transfer.dispatch", target_type="transfer", target_id=row["ref"])
    if row["status"] != "approved":
        raise Conflict(f"Transfer must be approved before dispatch (status: {row['status']}).")
    tlines = t.sql("SELECT * FROM motion.transfer_line WHERE transfer_id=%s ORDER BY id", (transfer_id,))
    override = {int(x["product_id"]): common.qty(x["quantity"]) for x in (lines or [])}
    src = common.branch(row["from_branch_id"])
    dst = common.branch(row["to_branch_id"])
    pids = [ln["product_id"] for ln in tlines]
    from .pos import _lock_guards
    _lock_guards(src["storage_location_id"], pids)
    on_hand = common.stock_on_hand([src["storage_location_id"]], pids)
    names = common.product_names(set(pids))
    send = {}
    for ln in tlines:
        q = override.get(ln["product_id"], common.qty(ln["requested_qty"]))
        if q < 0 or q > common.qty(ln["requested_qty"]):
            raise Invalid("Dispatch quantity must be between 0 and the approved quantity.")
        have = on_hand.get((src["storage_location_id"], ln["product_id"]), Decimal(0))
        if q > have:
            raise Conflict(f"Only {have} of {names[ln['product_id']]['name']} available at {src['name']}.")
        send[ln["product_id"]] = q
    if not any(send.values()):
        raise Invalid("Nothing to dispatch.")
    Shipment = t.model("stock.shipment.internal")
    uoms = _uoms(pids)
    cid = common.company_id()
    with t.set_context(**common.tryton_context(src["id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        shipment, = Shipment.create([{
            "company": cid, "from_location": src["storage_location_id"], "to_location": dst["storage_location_id"],
            "reference": row["ref"], "planned_date": common.today(), "planned_start_date": common.today(),
            "moves": [("create", [{"product": pid, "unit": uoms[pid], "quantity": float(q), "company": cid,
                                   "from_location": src["storage_location_id"], "to_location": dst["storage_location_id"]}
                                  for pid, q in send.items() if q])]}])
        Shipment.wait([shipment])
        Shipment.assign_force([shipment])
        Shipment.pack([shipment])  # 'ship' is only valid from 'packed'
        Shipment.ship([shipment])
        common.expect_state(shipment, "shipped", what="transfer dispatch")
    for pid, q in send.items():
        t.execute("UPDATE motion.transfer_line SET dispatched_qty=%s WHERE transfer_id=%s AND product_id=%s",
                  (q, transfer_id, pid))
    t.execute("""UPDATE motion.transfer SET status='dispatched', tryton_shipment_id=%s, dispatched_by=%s, dispatched_at=now()
                 WHERE id=%s""", (shipment.id, actor.user_id, transfer_id))
    approvals.mark_executed(row["approval_id"], actor=actor, result_type="stock.shipment.internal",
                            result_id=shipment.id, fulfilment_status="dispatched", final=False)
    record(actor=actor, action="transfer.dispatch", target_type="transfer", target_id=row["ref"], branch_id=src["id"],
           after={"shipment": shipment.number, "lines": {names[p]["name"]: str(q) for p, q in send.items()}})
    return get_transfer(actor, transfer_id)


def receive(actor: Principal, transfer_id: int, *, lines: list[dict], note: str | None = None) -> dict:
    actor.require("transfer.receive", action="transfer.receive")
    row = _transfer(transfer_id, lock=True)
    actor.require_branch(row["to_branch_id"], action="transfer.receive", target_type="transfer", target_id=row["ref"])
    if row["status"] != "dispatched":
        raise Conflict(f"Only dispatched transfers can be received (status: {row['status']}).")
    if row["dispatched_by"] == actor.user_id:
        raise Forbidden("The person who dispatched cannot also confirm receipt.", action="transfer.receive.self",
                        target_type="transfer", target_id=row["ref"])
    counts = {int(x["product_id"]): (common.qty(x["received_qty"]), common.qty(x.get("damaged_qty") or 0)) for x in lines}
    tlines = t.sql("SELECT * FROM motion.transfer_line WHERE transfer_id=%s ORDER BY id", (transfer_id,))
    Shipment = t.model("stock.shipment.internal")
    Move = t.model("stock.move")
    shipment = Shipment(row["tryton_shipment_id"])
    discrepancies = []
    costs = _cost([ln["product_id"] for ln in tlines])
    with t.set_context(**common.tryton_context(row["to_branch_id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        by_product = {m.product.id: m for m in shipment.incoming_moves}
        for ln in tlines:
            sent = common.qty(ln["dispatched_qty"] or 0)
            if not sent:
                continue
            got, damaged = counts.get(ln["product_id"], (sent, Decimal(0)))
            if got < 0 or damaged < 0 or got + damaged > sent:
                raise Invalid("Received + damaged cannot exceed the dispatched quantity.")
            move = by_product[ln["product_id"]]
            if got != sent:
                if got == 0:
                    Move.cancel([move])
                else:
                    Move.write([move], {"quantity": float(got)})
            short = sent - got - damaged
            for kind, q in (("transfer_damage", damaged), ("transfer_shortage", short)):
                if q > 0:
                    discrepancies.append((kind, ln["product_id"], q, common.money(q * costs.get(ln["product_id"], 0))))
            t.execute("UPDATE motion.transfer_line SET received_qty=%s, damaged_qty=%s WHERE id=%s", (got, damaged, ln["id"]))
        Shipment.do([shipment])
        common.expect_state(shipment, "done", what="transfer receipt")
    for kind, pid, q, value in discrepancies:
        t.execute("""INSERT INTO motion.discrepancy (kind, branch_id, source_type, source_id, product_id, quantity, amount,
                     detail, created_by) VALUES (%s,%s,'transfer',%s,%s,%s,%s,%s::jsonb,%s)""",
                  (kind, row["from_branch_id"], str(transfer_id), pid, q, value,
                   json.dumps({"ref": row["ref"], "note": note, "location": "in transit"}), actor.user_id))
    status = "received_with_discrepancy" if discrepancies else "received"
    t.execute("UPDATE motion.transfer SET status=%s, received_by=%s, received_at=now() WHERE id=%s",
              (status, actor.user_id, transfer_id))
    approvals.set_fulfilment(row["approval_id"], status)
    if not discrepancies:
        t.execute("UPDATE motion.approval_request SET status='executed', consumed_at=now() WHERE id=%s", (row["approval_id"],))
    record(actor=actor, action="transfer.receive", target_type="transfer", target_id=row["ref"],
           branch_id=row["to_branch_id"], reason=note,
           after={"status": status, "discrepancies": [{"kind": k, "product": p, "qty": str(q)} for k, p, q, _ in discrepancies]})
    return get_transfer(actor, transfer_id)


# ------------------------------------------------------------------ discrepancies & write-offs

def discrepancies(actor: Principal, *, status: str | None = "open", branch_id: int | None = None) -> list[dict]:
    actor.require_any("stock.view", "pos.shift.reconcile", "finance.view")
    rows = t.sql("""SELECT d.*, b.code AS branch_code, pt.name AS product_name FROM motion.discrepancy d
                    JOIN motion_branch b ON b.id=d.branch_id
                    LEFT JOIN product_product pp ON pp.id=d.product_id LEFT JOIN product_template pt ON pt.id=pp.template
                    WHERE (%s::text IS NULL OR d.status=%s) AND (%s::int IS NULL OR d.branch_id=%s)
                    ORDER BY d.created_at DESC LIMIT 300""", (status, status, branch_id, branch_id))
    return [r for r in rows if actor.can_branch(r["branch_id"])]


def request_writeoff(actor: Principal, *, branch_id: int, product_id: int, quantity: Decimal, reason: str,
                     discrepancy_id: int | None = None, kind: str = "damage") -> dict:
    """Request an authorised write-off.

    * from a branch's shelf (damage, expiry, theft) -> storage to lost & found;
    * or, with ``discrepancy_id`` of a transfer shortage/damage, of units still
      in transit -> transit to lost & found.
    """
    actor.require("stock.adjust.request", action="stock.writeoff.request")
    actor.require_branch(branch_id, action="stock.writeoff.request")
    q = common.qty(quantity)
    if q <= 0:
        raise Invalid("Quantity must be positive.")
    if not reason or len(reason.strip()) < 5:
        raise Invalid("Explain the write-off.")
    source = "shelf"
    if discrepancy_id:
        d = t.sql_one("SELECT * FROM motion.discrepancy WHERE id=%s FOR UPDATE", (discrepancy_id,))
        if not d or d["status"] != "open" or d["kind"] not in {"transfer_shortage", "transfer_damage"}:
            raise Conflict("Discrepancy is not open for write-off.")
        if d["branch_id"] != branch_id or d["product_id"] != product_id or q != common.qty(d["quantity"]):
            raise Invalid("Write-off must match the discrepancy.")
        source = "transit"
    else:
        b = common.branch(branch_id)
        have = common.stock_on_hand([b["storage_location_id"]], [product_id]).get((b["storage_location_id"], product_id), Decimal(0))
        if q > have:
            raise Conflict(f"Only {have} on hand.")
    cost = _cost([product_id]).get(product_id, Decimal(0))
    name = common.product_names({product_id})[product_id]["name"]
    req = approvals.submit(actor, action_type="stock_writeoff", branch_id=branch_id, amount=common.money(q * cost),
                           title=f"Write off {q} x {name} ({source})",
                           payload={"product_id": product_id, "quantity": str(q), "source": source, "kind": kind,
                                    "discrepancy_id": discrepancy_id, "product": name}, justification=reason)
    if discrepancy_id:
        t.execute("UPDATE motion.discrepancy SET status='pending_approval', approval_id=%s WHERE id=%s", (req["id"], discrepancy_id))
    return req


def execute_writeoff(actor: Principal, approval_id: int) -> dict:
    actor.require("stock.adjust.request", action="stock.writeoff.execute")
    req = approvals.require_approved(approval_id, "stock_writeoff")
    actor.require_branch(req["branch_id"], action="stock.writeoff.execute")
    if req["consumed_at"]:
        raise Conflict("Already executed.")
    p = req["payload"]
    pid, q = int(p["product_id"]), common.qty(p["quantity"])
    b = common.branch(req["branch_id"])
    from_loc = common.transit_location_id() if p["source"] == "transit" else b["storage_location_id"]
    Move = t.model("stock.move")
    with t.set_context(**common.tryton_context(b["id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        move, = Move.create([{"product": pid, "unit": _uoms([pid])[pid], "quantity": float(q), "company": common.company_id(),
                              "from_location": from_loc, "to_location": common.lost_found_location_id(),
                              "effective_date": common.today(), "origin": None}])
        Move.do([move])
        common.expect_state(move, "done", what="write-off move")
    approvals.mark_executed(approval_id, actor=actor, result_type="stock.move", result_id=move.id)
    if p.get("discrepancy_id"):
        t.execute("""UPDATE motion.discrepancy SET status='resolved', resolution='written_off', resolved_by=%s,
                     resolved_at=now(), resolution_note=%s WHERE id=%s""", (actor.user_id, req["ref"], int(p["discrepancy_id"])))
        _maybe_close_transfer(int(t.sql_one("SELECT source_id FROM motion.discrepancy WHERE id=%s", (int(p["discrepancy_id"]),))["source_id"]))
    record(actor=actor, action="stock.writeoff.execute", target_type="approval", target_id=req["ref"], branch_id=b["id"],
           amount=req["amount"], after={"product": p["product"], "quantity": str(q), "source": p["source"]})
    return approvals.get(actor, approval_id)


def receive_late(actor: Principal, discrepancy_id: int, quantity: Decimal, note: str) -> dict:
    """Units reported short later turn up: move them transit -> destination."""
    actor.require("transfer.receive", action="transfer.receive_late")
    d = t.sql_one("SELECT * FROM motion.discrepancy WHERE id=%s FOR UPDATE", (discrepancy_id,))
    if not d or d["status"] != "open" or d["kind"] != "transfer_shortage":
        raise Conflict("Discrepancy is not open.")
    tr = _transfer(int(d["source_id"]))
    actor.require_branch(tr["to_branch_id"], action="transfer.receive_late")
    q = common.qty(quantity)
    if q <= 0 or q > common.qty(d["quantity"]):
        raise Invalid("Quantity must be between 0 and the shortage.")
    dst = common.branch(tr["to_branch_id"])
    Move = t.model("stock.move")
    with t.set_context(**common.tryton_context(dst["id"])), t.transaction().set_user(actor.tryton_user_id or 0):
        move, = Move.create([{"product": d["product_id"], "unit": _uoms([d["product_id"]])[d["product_id"]],
                              "quantity": float(q), "company": common.company_id(),
                              "from_location": common.transit_location_id(), "to_location": dst["storage_location_id"],
                              "effective_date": common.today()}])
        Move.do([move])
    remaining = common.qty(d["quantity"]) - q
    if remaining:
        t.execute("UPDATE motion.discrepancy SET quantity=%s WHERE id=%s", (remaining, discrepancy_id))
    else:
        t.execute("""UPDATE motion.discrepancy SET status='resolved', resolution='found', resolved_by=%s, resolved_at=now(),
                     resolution_note=%s WHERE id=%s""", (actor.user_id, note, discrepancy_id))
    t.execute("UPDATE motion.transfer_line SET received_qty = COALESCE(received_qty,0) + %s WHERE transfer_id=%s AND product_id=%s",
              (q, tr["id"], d["product_id"]))
    _maybe_close_transfer(tr["id"])
    record(actor=actor, action="transfer.receive_late", target_type="transfer", target_id=tr["ref"], branch_id=dst["id"],
           reason=note, after={"quantity": str(q), "remaining_shortage": str(remaining)})
    return get_transfer(actor, tr["id"])


def _maybe_close_transfer(transfer_id: int) -> None:
    open_n = t.sql_one("""SELECT count(*) AS n FROM motion.discrepancy WHERE source_type='transfer' AND source_id=%s
                          AND status <> 'resolved'""", (str(transfer_id),))["n"]
    if open_n == 0:
        tr = _transfer(transfer_id)
        if tr["status"] == "received_with_discrepancy":
            t.execute("UPDATE motion.transfer SET status='closed' WHERE id=%s", (transfer_id,))
            t.execute("UPDATE motion.approval_request SET status='executed', fulfilment_status='closed', consumed_at=now() WHERE id=%s",
                      (tr["approval_id"],))


def record_count(actor: Principal, *, branch_id: int, counts: list[dict], note: str | None = None) -> dict:
    """Record a physical count; variances become open discrepancies (no silent adjustment)."""
    actor.require("stock.count", action="stock.count")
    actor.require_branch(branch_id, action="stock.count")
    b = common.branch(branch_id)
    pids = [int(c["product_id"]) for c in counts]
    on_hand = common.stock_on_hand([b["storage_location_id"]], pids)
    costs = _cost(pids)
    out = []
    for c in counts:
        pid = int(c["product_id"])
        counted = common.qty(c["counted"])
        system = on_hand.get((b["storage_location_id"], pid), Decimal(0))
        diff = counted - system
        out.append({"product_id": pid, "system": system, "counted": counted, "variance": diff})
        if diff:
            t.execute("""INSERT INTO motion.discrepancy (kind, branch_id, source_type, source_id, product_id, quantity, amount,
                         detail, created_by) VALUES ('count_variance',%s,'count',%s,%s,%s,%s,%s::jsonb,%s)""",
                      (branch_id, common.today().isoformat(), pid, diff, common.money(diff * costs.get(pid, 0)),
                       json.dumps({"system": str(system), "counted": str(counted), "note": note}), actor.user_id))
    record(actor=actor, action="stock.count", target_type="branch", target_id=branch_id, branch_id=branch_id, reason=note,
           after={"lines": len(out), "variances": sum(1 for o in out if o["variance"])})
    return {"lines": out}


@approvals.on_decision("transfer")
def _transfer_decided(req: dict) -> None:
    sync_transfer_approval(int(req["payload"]["transfer_id"]))


@approvals.on_decision("stock_writeoff")
def _writeoff_decided(req: dict) -> None:
    did = req["payload"].get("discrepancy_id")
    if did and req["status"] in {"rejected", "cancelled", "returned"}:
        t.execute("UPDATE motion.discrepancy SET status='open', approval_id=NULL WHERE id=%s AND status='pending_approval'",
                  (int(did),))
