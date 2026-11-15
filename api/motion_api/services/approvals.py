"""Configurable maker-checker approval engine.

Rules enforced here (server-side, never by the UI):
* default deny — no matching active policy => request cannot be submitted;
* the policy (steps) is snapshotted at submission; later policy edits do not
  silently change in-flight requests;
* strictly ordered steps; the actor must hold the step role (or an unexpired,
  audited delegation for it, within its amount ceiling) and branch scope;
* no self-approval, and one person cannot act on two steps of one request;
* reject / return-for-correction require a reason; resubmission restarts the
  chain at step 1 with an incremented revision (history is retained);
* approvals of single-use actions (refunds, till overrides) are consumed once.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from .. import tryton as t
from ..audit import record
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..permissions import ACTION_TYPES, APPROVER_ROLES
from ..security import Principal
from . import common

ACTION_LABELS = {
    "purchase_request": "Purchase requisition", "expense": "Expense", "supplier_payment": "Supplier payment",
    "transfer": "Stock transfer", "refund": "Refund", "stock_writeoff": "Stock write-off",
    "price_override": "Till price/discount override",
}
REF_PREFIX = {"purchase_request": "PR", "expense": "EXP", "supplier_payment": "PAY", "transfer": "TRQ",
              "refund": "RFD", "stock_writeoff": "WO", "price_override": "OVR"}


# action_type -> callables(request_row) run in the same transaction after a decision
ON_DECISION: dict[str, list] = {}


def on_decision(action_type: str):
    def deco(fn):
        ON_DECISION.setdefault(action_type, []).append(fn)
        return fn
    return deco


def _run_hooks(request_id: int) -> None:
    row = t.sql_one("SELECT * FROM motion.approval_request WHERE id=%s", (request_id,))
    for fn in ON_DECISION.get(row["action_type"], []):
        fn(row)


def policy_for(action_type: str, branch_id: int, amount: Decimal) -> dict | None:
    rows = t.sql("""
        SELECT * FROM motion.approval_policy
        WHERE active AND action_type=%s AND (branch_id IS NULL OR branch_id=%s)
          AND min_amount <= %s AND (max_amount IS NULL OR max_amount >= %s)
        ORDER BY (branch_id IS NULL), min_amount DESC LIMIT 1""", (action_type, branch_id, amount, amount))
    return rows[0] if rows else None


def _status_label(req: dict) -> str:
    if req["status"] == "pending":
        steps = req["steps"]
        idx = req["current_step"]
        return f"Awaiting {steps[idx]['label']}" if idx < len(steps) else "Pending"
    return {"approved": "Approved", "rejected": "Rejected", "returned": "Returned for correction",
            "cancelled": "Cancelled", "executed": "Completed"}.get(req["status"], req["status"])


def _decorate(req: dict) -> dict:
    req = dict(req)
    req["status_label"] = _status_label(req)
    req["action_label"] = ACTION_LABELS.get(req["action_type"], req["action_type"])
    req["step_count"] = len(req["steps"])
    return req


def submit(actor: Principal, *, action_type: str, branch_id: int, amount: Decimal, title: str,
           payload: dict[str, Any], justification: str | None = None, urgency: str = "normal",
           audit_action: str | None = None) -> dict:
    if action_type not in ACTION_TYPES:
        raise Invalid("Unknown approval type.")
    actor.require_branch(branch_id, action=f"approval.{action_type}.submit")
    amount = common.money(amount)
    if amount < 0:
        raise Invalid("Amount cannot be negative.")
    policy = policy_for(action_type, branch_id, amount)
    if not policy:
        raise Forbidden("No approval authority is configured for this amount (default deny). "
                        "Ask the Head Manager to configure an approval policy.",
                        action=f"approval.{action_type}.no_policy", branch_id=branch_id)
    steps = policy["steps"]
    bad = {s["role"] for s in steps} - APPROVER_ROLES[action_type]
    if bad:
        raise Forbidden(f"Policy names roles that may not approve {action_type}: {sorted(bad)}",
                        action="approval.policy_invalid")
    ref = common.next_ref(REF_PREFIX[action_type], "approval_ref_seq")
    row = t.sql_one("""
        INSERT INTO motion.approval_request (ref, action_type, branch_id, amount, title, justification, urgency,
            payload, status, current_step, steps, policy_id, initiator_id, due_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'pending',0,%s::jsonb,%s,%s, now() + interval '3 days')
        RETURNING *""",
        (ref, action_type, branch_id, amount, title, justification, urgency, json.dumps(payload, default=str),
         json.dumps(steps), policy["id"], actor.user_id))
    t.execute("""INSERT INTO motion.approval_action (request_id, step_index, actor_id, action, comment, from_status,
                 to_status, payload_snapshot) VALUES (%s,NULL,%s,'submit',%s,NULL,'pending',%s::jsonb)""",
              (row["id"], actor.user_id, justification, json.dumps(payload, default=str)))
    record(actor=actor, action=audit_action or f"approval.{action_type}.submit", target_type="approval",
           target_id=row["ref"], branch_id=branch_id, amount=amount, reason=justification,
           after={"steps": [s["label"] for s in steps], "title": title})
    return _decorate(row)


def _load_for_update(request_id: int) -> dict:
    row = t.sql_one("SELECT * FROM motion.approval_request WHERE id=%s FOR UPDATE", (request_id,))
    if not row:
        raise NotFound("Approval request not found.")
    return row


def _authority(actor: Principal, role: str, amount: Decimal) -> tuple[str, int | None] | None:
    """Return (acting_role, delegated_from) when actor may act for ``role``."""
    if role in actor.roles:
        return role, None
    d = t.sql_one("""
        SELECT d.* FROM motion.delegation d
        JOIN motion.user_role ur ON ur.user_id = d.from_user AND ur.role_code = d.role_code
        JOIN motion.app_user fu ON fu.id = d.from_user AND fu.status='active'
        WHERE d.to_user=%s AND d.role_code=%s AND d.revoked_at IS NULL AND now() BETWEEN d.starts_at AND d.ends_at
          AND (d.max_amount IS NULL OR d.max_amount >= %s)
        ORDER BY d.ends_at DESC LIMIT 1""", (actor.user_id, role, amount))
    if d:
        return role, d["from_user"]
    return None


def act(actor: Principal, request_id: int, *, decision: str, comment: str | None) -> dict:
    if decision not in {"approve", "reject", "return"}:
        raise Invalid("Decision must be approve, reject or return.")
    req = _load_for_update(request_id)
    audit_action = f"approval.{req['action_type']}.{decision}"
    if not actor.can_branch(req["branch_id"]):
        raise Forbidden("This request belongs to a branch you are not assigned to.", action=audit_action,
                        target_type="approval", target_id=req["ref"], branch_id=req["branch_id"])
    if req["status"] != "pending":
        raise Conflict(f"This request is already {req['status']}.")
    if req["initiator_id"] == actor.user_id:
        raise Forbidden("You cannot approve or reject your own request.", action=audit_action + ".self",
                        target_type="approval", target_id=req["ref"], branch_id=req["branch_id"])
    steps = req["steps"]
    idx = req["current_step"]
    step = steps[idx]
    authority = _authority(actor, step["role"], req["amount"])
    if not authority:
        raise Forbidden(f"This step needs: {step['label']} ({step['role']}).", action=audit_action,
                        target_type="approval", target_id=req["ref"], branch_id=req["branch_id"])
    prior = t.sql_one("""SELECT 1 AS x FROM motion.approval_action WHERE request_id=%s AND actor_id=%s
                         AND action='approve' AND step_index IS NOT NULL
                         AND at > (SELECT max(at) FROM motion.approval_action WHERE request_id=%s AND action IN ('submit','resubmit'))""",
                      (request_id, actor.user_id, request_id))
    if prior and decision == "approve":
        raise Forbidden("A different person must approve each step of a request.", action=audit_action + ".duplicate_approver",
                        target_type="approval", target_id=req["ref"], branch_id=req["branch_id"])
    if decision in {"reject", "return"} and not (comment and comment.strip()):
        raise Invalid("A reason is required.")
    from_status = req["status"]
    if decision == "approve":
        if idx + 1 >= len(steps):
            new_status, new_step = "approved", idx + 1
        else:
            new_status, new_step = "pending", idx + 1
    elif decision == "reject":
        new_status, new_step = "rejected", idx
    else:
        new_status, new_step = "returned", idx
    t.execute("UPDATE motion.approval_request SET status=%s, current_step=%s, updated_at=now() WHERE id=%s",
              (new_status, new_step, request_id))
    t.execute("""INSERT INTO motion.approval_action (request_id, step_index, actor_id, acting_role, delegated_from,
                 action, comment, from_status, to_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (request_id, idx, actor.user_id, authority[0], authority[1], decision, comment, from_status, new_status))
    record(actor=actor, action=audit_action, target_type="approval", target_id=req["ref"], branch_id=req["branch_id"],
           amount=req["amount"], reason=comment,
           before={"status": from_status, "step": step["label"]},
           after={"status": new_status, "delegated_from": authority[1]})
    if new_status != "pending":
        _run_hooks(request_id)
    return get(actor, request_id, _skip_visibility=True)


def resubmit(actor: Principal, request_id: int, *, payload: dict, amount: Decimal, title: str | None,
             justification: str | None) -> dict:
    req = _load_for_update(request_id)
    if req["initiator_id"] != actor.user_id:
        raise Forbidden("Only the person who raised the request can resubmit it.", action="approval.resubmit",
                        target_type="approval", target_id=req["ref"])
    if req["status"] != "returned":
        raise Conflict("Only requests returned for correction can be resubmitted.")
    amount = common.money(amount)
    policy = policy_for(req["action_type"], req["branch_id"], amount)
    if not policy:
        raise Forbidden("No approval authority is configured for this amount (default deny).",
                        action="approval.no_policy", branch_id=req["branch_id"])
    t.execute("""UPDATE motion.approval_request SET payload=%s::jsonb, amount=%s, title=COALESCE(%s,title),
                 justification=COALESCE(%s, justification), status='pending', current_step=0, steps=%s::jsonb,
                 policy_id=%s, revision=revision+1, updated_at=now() WHERE id=%s""",
              (json.dumps(payload, default=str), amount, title, justification, json.dumps(policy["steps"]),
               policy["id"], request_id))
    t.execute("""INSERT INTO motion.approval_action (request_id, actor_id, action, comment, from_status, to_status,
                 payload_snapshot) VALUES (%s,%s,'resubmit',%s,'returned','pending',%s::jsonb)""",
              (request_id, actor.user_id, justification, json.dumps(payload, default=str)))
    record(actor=actor, action=f"approval.{req['action_type']}.resubmit", target_type="approval",
           target_id=req["ref"], branch_id=req["branch_id"], amount=amount,
           before={"amount": str(req["amount"]), "payload": req["payload"]}, after={"amount": str(amount), "payload": payload})
    return get(actor, request_id)


def cancel(actor: Principal, request_id: int, reason: str) -> dict:
    req = _load_for_update(request_id)
    if req["initiator_id"] != actor.user_id and "owner" not in actor.roles:
        raise Forbidden("Only the requester or the Head Manager can cancel.", action="approval.cancel",
                        target_type="approval", target_id=req["ref"])
    if req["status"] not in {"pending", "returned", "approved"} or req["fulfilment_status"]:
        raise Conflict("This request can no longer be cancelled.")
    t.execute("UPDATE motion.approval_request SET status='cancelled', updated_at=now() WHERE id=%s", (request_id,))
    t.execute("""INSERT INTO motion.approval_action (request_id, actor_id, action, comment, from_status, to_status)
                 VALUES (%s,%s,'cancel',%s,%s,'cancelled')""", (request_id, actor.user_id, reason, req["status"]))
    record(actor=actor, action=f"approval.{req['action_type']}.cancel", target_type="approval", target_id=req["ref"],
           branch_id=req["branch_id"], reason=reason)
    _run_hooks(request_id)
    return get(actor, request_id)


def require_approved(request_id: int, action_type: str, *, branch_id: int | None = None) -> dict:
    req = _load_for_update(request_id)
    if req["action_type"] != action_type:
        raise Invalid("Approval does not match this action.")
    if req["status"] == "executed" or req["consumed_at"]:
        raise Conflict("This approval has already been used.")
    if req["status"] != "approved":
        raise Forbidden("This action has not been approved yet.", action=f"{action_type}.execute_unapproved",
                        target_type="approval", target_id=req["ref"])
    if branch_id is not None and req["branch_id"] != branch_id:
        raise Forbidden("Approval belongs to a different branch.", action=f"{action_type}.execute_wrong_branch",
                        target_type="approval", target_id=req["ref"])
    return req


def mark_executed(request_id: int, *, actor: Principal, result_type: str, result_id: Any,
                  fulfilment_status: str | None = None, final: bool = True) -> None:
    t.execute("""UPDATE motion.approval_request SET status=CASE WHEN %s THEN 'executed' ELSE status END,
                 result_type=%s, result_id=%s, fulfilment_status=COALESCE(%s, fulfilment_status),
                 consumed_at=CASE WHEN %s THEN now() ELSE consumed_at END, updated_at=now() WHERE id=%s""",
              (final, result_type, str(result_id), fulfilment_status, final, request_id))
    t.execute("""INSERT INTO motion.approval_action (request_id, actor_id, action, comment, from_status, to_status)
                 VALUES (%s,%s,'execute',%s,'approved',%s)""",
              (request_id, actor.user_id, f"{result_type} {result_id}", "executed" if final else "approved"))


def set_fulfilment(request_id: int, status: str) -> None:
    t.execute("UPDATE motion.approval_request SET fulfilment_status=%s, updated_at=now() WHERE id=%s", (status, request_id))


def get(actor: Principal, request_id: int, _skip_visibility: bool = False) -> dict:
    row = t.sql_one("""SELECT r.*, u.full_name AS initiator_name, b.name AS branch_name
                       FROM motion.approval_request r JOIN motion.app_user u ON u.id=r.initiator_id
                       JOIN motion_branch b ON b.id=r.branch_id WHERE r.id=%s""", (request_id,))
    if not row:
        raise NotFound("Approval request not found.")
    if not _skip_visibility:
        visible = row["initiator_id"] == actor.user_id or (
            actor.can_branch(row["branch_id"]) and (actor.has("approvals.inbox") or actor.has("audit.view")))
        if not visible:
            raise Forbidden("You cannot view this request.", action="approval.view", target_type="approval",
                            target_id=row["ref"], branch_id=row["branch_id"])
    row = _decorate(row)
    row["actions"] = t.sql("""
        SELECT a.at, a.action, a.comment, a.step_index, a.acting_role, a.from_status, a.to_status,
               u.full_name AS actor_name, du.full_name AS delegated_from_name
        FROM motion.approval_action a JOIN motion.app_user u ON u.id=a.actor_id
        LEFT JOIN motion.app_user du ON du.id=a.delegated_from
        WHERE a.request_id=%s ORDER BY a.at, a.id""", (request_id,))
    me_step = None
    if row["status"] == "pending" and row["initiator_id"] != actor.user_id and actor.can_branch(row["branch_id"]):
        step = row["steps"][row["current_step"]]
        if _authority(actor, step["role"], row["amount"]):
            me_step = step
    row["can_act"] = me_step is not None
    return row


def inbox(actor: Principal) -> dict:
    actor.require("approvals.inbox")
    pending = t.sql("""
        SELECT r.*, u.full_name AS initiator_name, b.name AS branch_name
        FROM motion.approval_request r JOIN motion.app_user u ON u.id=r.initiator_id
        JOIN motion_branch b ON b.id=r.branch_id
        WHERE r.status='pending' ORDER BY r.urgency='critical' DESC, r.urgency='high' DESC, r.created_at""")
    actionable, watching = [], []
    for r in pending:
        if not actor.can_branch(r["branch_id"]):
            continue
        step = r["steps"][r["current_step"]]
        d = _decorate(r)
        d["overdue"] = bool(r["due_at"] and r["due_at"] < common_now())
        if r["initiator_id"] != actor.user_id and _authority(actor, step["role"], r["amount"]):
            actionable.append(d)
        else:
            watching.append(d)
    mine = [_decorate(r) for r in t.sql("""
        SELECT r.*, b.name AS branch_name FROM motion.approval_request r JOIN motion_branch b ON b.id=r.branch_id
        WHERE r.initiator_id=%s ORDER BY r.updated_at DESC LIMIT 50""", (actor.user_id,))]
    return {"actionable": actionable, "watching": watching[:50], "mine": mine}


def history(actor: Principal, *, action_type: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
    actor.require_any("approvals.inbox", "audit.view")
    rows = t.sql("""
        SELECT r.*, u.full_name AS initiator_name, b.name AS branch_name
        FROM motion.approval_request r JOIN motion.app_user u ON u.id=r.initiator_id JOIN motion_branch b ON b.id=r.branch_id
        WHERE (%s::text IS NULL OR r.action_type=%s) AND (%s::text IS NULL OR r.status=%s)
        ORDER BY r.updated_at DESC LIMIT %s""", (action_type, action_type, status, status, limit))
    return [_decorate(r) for r in rows if actor.can_branch(r["branch_id"]) or r["initiator_id"] == actor.user_id]


def common_now():
    from ..security import utcnow

    return utcnow()


# ---------------------------------------------------------------- policies & delegation

def list_policies(actor: Principal) -> list[dict]:
    actor.require_any("admin.policies", "approvals.inbox")
    return t.sql("SELECT * FROM motion.approval_policy WHERE active ORDER BY action_type, min_amount")


def replace_policy(actor: Principal, policy_id: int, *, min_amount: Decimal, max_amount: Decimal | None,
                   steps: list[dict], reason: str) -> dict:
    actor.require("admin.policies", action="admin.policy.change")
    old = t.sql_one("SELECT * FROM motion.approval_policy WHERE id=%s AND active FOR UPDATE", (policy_id,))
    if not old:
        raise NotFound("Policy not found.")
    if not reason or len(reason.strip()) < 5:
        raise Invalid("Explain why the approval policy is changing.")
    if not steps:
        raise Invalid("A policy needs at least one step.")
    for s in steps:
        if s.get("role") not in APPROVER_ROLES[old["action_type"]]:
            raise Invalid(f"Role {s.get('role')} cannot approve {old['action_type']}.")
        s.setdefault("label", s["role"].replace("_", " ").title())
        s.setdefault("kind", "approve")
    if max_amount is not None and max_amount < min_amount:
        raise Invalid("Maximum must be greater than minimum.")
    t.execute("UPDATE motion.approval_policy SET active=false WHERE id=%s", (policy_id,))
    new = t.sql_one("""INSERT INTO motion.approval_policy (action_type, branch_id, min_amount, max_amount, steps, note, updated_by)
                       VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s) RETURNING *""",
                    (old["action_type"], old["branch_id"], min_amount, max_amount, json.dumps(steps), reason, actor.user_id))
    record(actor=actor, action="admin.policy.change", target_type="approval_policy", target_id=new["id"], reason=reason,
           before={"id": old["id"], "min": str(old["min_amount"]), "max": str(old["max_amount"]), "steps": old["steps"]},
           after={"id": new["id"], "min": str(min_amount), "max": str(max_amount), "steps": steps})
    return new


def create_delegation(actor: Principal, *, from_user: int, to_user: int, role_code: str, max_amount: Decimal | None,
                      starts_at, ends_at, reason: str) -> dict:
    actor.require("admin.users", action="admin.delegation.create")
    if to_user == actor.user_id:
        raise Forbidden("You cannot delegate authority to yourself.", action="admin.delegation.self")
    if not t.sql_one("SELECT 1 AS x FROM motion.user_role WHERE user_id=%s AND role_code=%s", (from_user, role_code)):
        raise Invalid("The delegating user does not hold that role.")
    if (ends_at - starts_at).days > 31:
        raise Invalid("Delegations are limited to 31 days.")
    row = t.sql_one("""INSERT INTO motion.delegation (from_user, to_user, role_code, max_amount, starts_at, ends_at, reason, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (from_user, to_user, role_code, max_amount, starts_at, ends_at, reason, actor.user_id))
    record(actor=actor, action="admin.delegation.create", target_type="delegation", target_id=row["id"], reason=reason,
           after={"from": from_user, "to": to_user, "role": role_code, "max": str(max_amount), "ends": str(ends_at)})
    return row


def list_delegations(actor: Principal) -> list[dict]:
    actor.require("admin.users")
    return t.sql("""SELECT d.*, f.full_name AS from_name, tu.full_name AS to_name FROM motion.delegation d
                    JOIN motion.app_user f ON f.id=d.from_user JOIN motion.app_user tu ON tu.id=d.to_user
                    ORDER BY d.created_at DESC LIMIT 100""")


def revoke_delegation(actor: Principal, delegation_id: int, reason: str) -> None:
    actor.require("admin.users", action="admin.delegation.revoke")
    n = t.execute("UPDATE motion.delegation SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL", (delegation_id,))
    if not n:
        raise NotFound("Delegation not found or already revoked.")
    record(actor=actor, action="admin.delegation.revoke", target_type="delegation", target_id=delegation_id, reason=reason)


def mine(actor: Principal, action_type: str | None = None) -> list[dict]:
    """Requests raised by the caller (no inbox permission needed: e.g. cashier refund requests)."""
    rows = t.sql("""SELECT r.*, b.name AS branch_name FROM motion.approval_request r JOIN motion_branch b ON b.id=r.branch_id
                    WHERE r.initiator_id=%s AND (%s::text IS NULL OR r.action_type=%s)
                    ORDER BY r.updated_at DESC LIMIT 50""", (actor.user_id, action_type, action_type))
    return [_decorate(r) for r in rows]
