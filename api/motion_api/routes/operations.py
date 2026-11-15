"""Approvals, stock & transfers, procurement, expenses/payments, finance, dashboards, insights, edge sync."""
from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import tryton as t
from ..audit import record
from ..errors import AppError, Invalid
from ..security import Principal
from ..services import ai, approvals, catalog, dashboards, finance, insights, pos, purchasing, stock, sync
from ..web import MotionJSON, unit_of_work

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ approvals

class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|return)$")
    comment: str | None = Field(default=None, max_length=1000)


class ResubmitIn(BaseModel):
    payload: dict
    amount: Decimal = Field(ge=0)
    title: str | None = None
    justification: str | None = None


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.get("/approvals/inbox")
def inbox(request: Request):
    return MotionJSON(unit_of_work(request, lambda p: approvals.inbox(p)))


@router.get("/approvals/mine")
def my_requests(request: Request, action_type: str | None = None):
    return MotionJSON({"requests": unit_of_work(request, lambda p: approvals.mine(p, action_type))})


@router.get("/approvals/history")
def history(request: Request, action_type: str | None = None, status: str | None = None):
    return MotionJSON({"requests": unit_of_work(request, lambda p: approvals.history(p, action_type=action_type,
                                                                                         status=status))})


@router.get("/approvals/{request_id}")
def get_request(request_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: approvals.get(p, request_id)))


@router.post("/approvals/{request_id}/decision")
def decide(request_id: int, body: DecisionIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: approvals.act(p, request_id, decision=body.decision,
                                                                      comment=body.comment)))


@router.post("/approvals/{request_id}/resubmit")
def resubmit(request_id: int, body: ResubmitIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: approvals.resubmit(
        p, request_id, payload=body.payload, amount=body.amount, title=body.title, justification=body.justification)))


@router.post("/approvals/{request_id}/cancel")
def cancel(request_id: int, body: CancelIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: approvals.cancel(p, request_id, body.reason)))


# ------------------------------------------------------------------ stock & transfers

class QtyLineIn(BaseModel):
    product_id: int
    quantity: Decimal = Field(gt=0)


class TransferIn(BaseModel):
    from_branch_id: int
    to_branch_id: int
    lines: list[QtyLineIn] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=500)


class DispatchIn(BaseModel):
    lines: list[QtyLineIn] | None = None


class ReceiveLineIn(BaseModel):
    product_id: int
    received_qty: Decimal = Field(ge=0)
    damaged_qty: Decimal = Field(default=Decimal(0), ge=0)


class ReceiveIn(BaseModel):
    lines: list[ReceiveLineIn]
    note: str | None = None


class WriteoffIn(BaseModel):
    branch_id: int
    product_id: int
    quantity: Decimal = Field(gt=0)
    reason: str = Field(min_length=5, max_length=500)
    discrepancy_id: int | None = None
    kind: str = "damage"


class LateIn(BaseModel):
    quantity: Decimal = Field(gt=0)
    note: str = Field(min_length=3)


class CountLineIn(BaseModel):
    product_id: int
    counted: Decimal = Field(ge=0)


class CountIn(BaseModel):
    branch_id: int
    counts: list[CountLineIn] = Field(min_length=1)
    note: str | None = None


@router.get("/stock/overview")
def stock_overview(request: Request, branch_id: int | None = None):
    return MotionJSON(unit_of_work(request, lambda p: stock.overview(p, branch_id)))


@router.get("/stock/movements")
def stock_movements(request: Request, branch_id: int, product_id: int | None = None, days: int = 30):
    return MotionJSON({"movements": unit_of_work(request, lambda p: stock.movements(
        p, branch_id=branch_id, product_id=product_id, days=min(days, 365)))})


@router.get("/stock/discrepancies")
def stock_discrepancies(request: Request, status: str | None = "open", branch_id: int | None = None):
    return MotionJSON({"discrepancies": unit_of_work(request, lambda p: stock.discrepancies(
        p, status=status if status != "all" else None, branch_id=branch_id))})


@router.get("/transfers")
def transfers(request: Request, status: str | None = None):
    return MotionJSON({"transfers": unit_of_work(request, lambda p: stock.list_transfers(p, status))})


@router.get("/transfers/{transfer_id}")
def transfer(transfer_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: stock.get_transfer(p, transfer_id)))


@router.post("/transfers")
def transfer_request(body: TransferIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: stock.request_transfer(
        p, from_branch_id=body.from_branch_id, to_branch_id=body.to_branch_id,
        lines=[x.model_dump() for x in body.lines], reason=body.reason)), status_code=201)


@router.post("/transfers/{transfer_id}/dispatch")
def transfer_dispatch(transfer_id: int, body: DispatchIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: stock.dispatch(
        p, transfer_id, lines=[x.model_dump() for x in body.lines] if body.lines else None)))


@router.post("/transfers/{transfer_id}/receive")
def transfer_receive(transfer_id: int, body: ReceiveIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: stock.receive(
        p, transfer_id, lines=[x.model_dump() for x in body.lines], note=body.note)))


@router.post("/stock/discrepancies/{discrepancy_id}/receive-late")
def discrepancy_late(discrepancy_id: int, body: LateIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: stock.receive_late(p, discrepancy_id, body.quantity, body.note)))


@router.post("/stock/writeoffs")
def writeoff_request(body: WriteoffIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: stock.request_writeoff(p, **body.model_dump())), status_code=201)


@router.post("/stock/writeoffs/{approval_id}/execute")
def writeoff_execute(approval_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: stock.execute_writeoff(p, approval_id)))


@router.post("/stock/counts")
def stock_count(body: CountIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: stock.record_count(
        p, branch_id=body.branch_id, counts=[x.model_dump() for x in body.counts], note=body.note)), status_code=201)


# ------------------------------------------------------------------ procurement, invoices, payments, expenses

class ReqLineIn(BaseModel):
    product_id: int
    quantity: Decimal = Field(gt=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)


class RequisitionIn(BaseModel):
    branch_id: int
    supplier_id: int
    lines: list[ReqLineIn] = Field(min_length=1, max_length=100)
    justification: str = Field(min_length=5, max_length=1000)
    urgency: str = Field(default="normal", pattern="^(low|normal|high|critical)$")


class GoodsLineIn(BaseModel):
    line_id: int
    received_qty: Decimal = Field(ge=0)
    lot_ref: str | None = None
    expiry_date: dt.date | None = None


class GoodsIn(BaseModel):
    lines: list[GoodsLineIn] = Field(min_length=1)
    note: str | None = None


class PostInvoiceIn(BaseModel):
    supplier_reference: str = Field(min_length=1, max_length=60)
    invoice_date: dt.date


class PaymentRequestIn(BaseModel):
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)


class RecordPaymentIn(BaseModel):
    method: str = "Bank transfer (TEST bank)"
    paid_on: dt.date | None = None


class ExpenseIn(BaseModel):
    branch_id: int
    category: str
    payee: str = Field(min_length=2, max_length=120)
    amount: Decimal = Field(gt=0)
    description: str = Field(min_length=3, max_length=500)
    document_ref: str | None = Field(default=None, max_length=60)
    incurred_on: dt.date


class PostExpenseIn(BaseModel):
    posting_date: dt.date | None = None


@router.get("/procurement/suppliers")
def suppliers(request: Request):
    return MotionJSON({"suppliers": unit_of_work(request, lambda p: purchasing.suppliers(p))})


@router.post("/procurement/requisitions")
def requisition(body: RequisitionIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.submit_requisition(
        p, branch_id=body.branch_id, supplier_id=body.supplier_id, lines=[x.model_dump() for x in body.lines],
        justification=body.justification, urgency=body.urgency)), status_code=201)


@router.get("/procurement/evidence")
def evidence(request: Request, branch_id: int, product_ids: str):
    def fn(p: Principal):
        p.require("procurement.request")
        p.require_branch(branch_id, action="procurement.evidence")
        try:
            ids = [int(x) for x in product_ids.split(",") if x]
        except ValueError as exc:
            raise Invalid("product_ids must be comma-separated integers.") from exc
        return {"evidence": purchasing.requisition_evidence(branch_id, ids, p)}
    return MotionJSON(unit_of_work(request, fn))


@router.post("/procurement/requisitions/{request_id}/issue-po")
def issue_po(request_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.issue_po(p, request_id)))


@router.get("/procurement/orders")
def orders(request: Request):
    return MotionJSON({"orders": unit_of_work(request, lambda p: purchasing.open_purchase_orders(p))})


@router.get("/procurement/orders/{purchase_id}")
def order(purchase_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.po_detail(p, purchase_id)))


@router.post("/procurement/orders/{purchase_id}/receive")
def receive_goods(purchase_id: int, body: GoodsIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.receive_goods(
        p, purchase_id, lines=[x.model_dump() for x in body.lines], note=body.note)))


@router.get("/finance/payables")
def payables(request: Request, state: str | None = None):
    return MotionJSON({"invoices": unit_of_work(request, lambda p: purchasing.payables(p, state=state))})


@router.get("/finance/invoices/{invoice_id}")
def invoice(invoice_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.invoice_detail(p, invoice_id)))


@router.post("/finance/invoices/{invoice_id}/post")
def post_invoice(invoice_id: int, body: PostInvoiceIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.post_invoice(
        p, invoice_id, supplier_reference=body.supplier_reference, invoice_date=body.invoice_date)))


@router.post("/finance/invoices/{invoice_id}/payment-request")
def payment_request(invoice_id: int, body: PaymentRequestIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.request_payment(
        p, invoice_id, amount=body.amount, reason=body.reason)), status_code=201)


@router.post("/finance/payments/{request_id}/record")
def record_payment(request_id: int, body: RecordPaymentIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.record_payment(
        p, request_id, method=body.method, paid_on=body.paid_on)))


@router.post("/finance/expenses")
def expense(body: ExpenseIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.submit_expense(p, **body.model_dump())), status_code=201)


@router.post("/finance/expenses/{request_id}/post")
def post_expense(request_id: int, body: PostExpenseIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: purchasing.post_expense(p, request_id, posting_date=body.posting_date)))


# ------------------------------------------------------------------ finance & reports

@router.get("/finance/pnl")
def pnl(request: Request, period: str = "month", branch_id: int | None = None, date_from: dt.date | None = None,
        date_to: dt.date | None = None):
    return MotionJSON(unit_of_work(request, lambda p: finance.profit_and_loss(
        p, period=period, branch_id=branch_id, date_from=date_from, date_to=date_to)))


@router.get("/finance/series")
def series(request: Request, days: int = 30, branch_id: int | None = None):
    return MotionJSON({"series": unit_of_work(request, lambda p: finance.daily_series(
        p, days=min(days, 366), branch_id=branch_id))})


@router.get("/finance/sales")
def sales_kpis(request: Request, period: str = "month", branch_id: int | None = None, date_from: dt.date | None = None,
               date_to: dt.date | None = None):
    def fn(p: Principal):
        p.require_any("finance.view", "dashboard.branch")
        d0, d1, label = finance.period_bounds(period, date_from, date_to)
        ids = finance._scope(p, branch_id)[0]
        return {"period": {"label": label, "from": d0, "to": d1}, **finance.sales_kpis(p, d0=d0, d1=d1, branch_ids=ids)}
    return MotionJSON(unit_of_work(request, fn))


@router.get("/finance/cash")
def cash(request: Request):
    return MotionJSON(unit_of_work(request, lambda p: finance.cash_position(p)))


@router.get("/finance/break-even")
def break_even(request: Request, period: str = "last30", branch_id: int | None = None, date_from: dt.date | None = None,
               date_to: dt.date | None = None):
    return MotionJSON(unit_of_work(request, lambda p: finance.break_even(
        p, period=period, branch_id=branch_id, date_from=date_from, date_to=date_to)))


@router.get("/finance/capital-recovery")
def capital_recovery(request: Request):
    return MotionJSON(unit_of_work(request, lambda p: finance.capital_recovery(p)))


class InvestmentIn(BaseModel):
    label: str = Field(min_length=3, max_length=200)
    amount: Decimal = Field(gt=0)
    invested_on: dt.date
    note: str | None = None


@router.post("/finance/investments")
def add_investment(body: InvestmentIn, request: Request):
    def fn(p: Principal):
        p.require("finance.investment", action="finance.investment.add")
        finance.require_company_wide(p, "finance.investment.add")
        row = t.sql_one("""INSERT INTO motion.investment (label, amount, invested_on, note, created_by)
                           VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                        (body.label, body.amount, body.invested_on, body.note, p.user_id))
        record(actor=p, action="finance.investment.add", target_type="investment", target_id=row["id"],
               amount=body.amount, after={"label": body.label, "invested_on": body.invested_on})
        return row
    return MotionJSON(unit_of_work(request, fn), status_code=201)


class DepositIn(BaseModel):
    branch_id: int
    amount: Decimal = Field(gt=0)
    date: dt.date
    reference: str = Field(min_length=3, max_length=60)


@router.post("/finance/bank-deposits")
def bank_deposit(body: DepositIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: finance.bank_deposit(
        p, branch_id=body.branch_id, amount=body.amount, date=body.date, reference=body.reference)), status_code=201)


@router.get("/finance/ledger")
def ledger(request: Request, account_code: str | None = None, branch_id: int | None = None, days: int = 14):
    return MotionJSON({"lines": unit_of_work(request, lambda p: finance.ledger(
        p, account_code=account_code, branch_id=branch_id, days=min(days, 366)))})


@router.get("/finance/trial-balance")
def trial_balance(request: Request):
    return MotionJSON(unit_of_work(request, lambda p: finance.trial_balance(p)))


@router.get("/reports/{name}.csv")
def report_csv(name: str, request: Request, period: str = "month", branch_id: int | None = None):
    def fn(p: Principal):
        p.require("reports.export", action=f"reports.export.{name}")
        if name == "pnl":
            data = finance.profit_and_loss(p, period=period, branch_id=branch_id)
            head = ["branch", *finance.LINE_ORDER]
            rows = [[b["code"], *[b[k] for k in finance.LINE_ORDER]] for b in data["branches"]]
            rows.append(["CONSOLIDATED", *[data["consolidated"][k] for k in finance.LINE_ORDER]])
        elif name == "stock":
            data = catalog.list_products(p, branch_id=branch_id)
            codes = [b["code"] for b in data["branches"]]
            head = ["sku", "name", "category", "uom", *codes]
            rows = [[i["sku"], i["name"], i["category"], i["uom"], *[i["stock"][c] for c in codes]] for i in data["items"]]
        elif name == "shifts":
            data = pos.list_shifts(p, branch_id=branch_id, limit=1000)
            head = ["id", "branch", "register", "cashier", "opened_at", "closed_at", "status", "expected", "counted", "variance"]
            rows = [[s["id"], s["branch_code"], s["register_name"], s["cashier_name"], s["opened_at"], s["closed_at"],
                     s["status"], s["expected_cash"], s["counted_cash"], s["variance"]] for s in data]
        else:
            raise AppError("Unknown report.")
        record(actor=p, action=f"reports.export.{name}", branch_id=branch_id, reason=f"{len(rows)} rows, period={period}")
        return head, rows
    head, rows = unit_of_work(request, fn, csrf=False)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(head)
    writer.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}.csv"})


# ------------------------------------------------------------------ dashboards & insights

@router.get("/dashboard")
def dashboard(request: Request, role: str | None = None, branch_id: int | None = None):
    return MotionJSON(unit_of_work(request, lambda p: dashboards.build(p, role=role, branch_id=branch_id)))


@router.get("/insights")
def insight_list(request: Request, branch_id: int | None = None):
    return MotionJSON({"insights": unit_of_work(request, lambda p: insights.generate(p, branch_id)),
                       "method": insights.METHOD})


class AckIn(BaseModel):
    key: str = Field(min_length=3, max_length=200)
    status: str = Field(pattern="^(acknowledged|dismissed)$")
    note: str | None = None


@router.post("/insights/ack")
def insight_ack(body: AckIn, request: Request):
    unit_of_work(request, lambda p: insights.acknowledge(p, body.key, body.status, body.note))
    return MotionJSON({"ok": True})


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(min_length=2, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


@router.get("/ai/status")
def ai_status(request: Request):
    return MotionJSON(unit_of_work(request, lambda p: ai.status(p)))


@router.post("/ai/chat")
def ai_chat(body: ChatIn, request: Request):
    # Authenticate (+CSRF) in a short transaction; the provider call happens outside any
    # database transaction and each tool opens its own short read-only one.
    principal = unit_of_work(request, lambda p: p)
    return MotionJSON(ai.chat(principal, body.message, [h.model_dump() for h in body.history]))


@router.post("/insights/ask")
def insight_ask(body: AskIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: insights.ask(p, body.question)))


# ------------------------------------------------------------------ edge devices (admin) and edge sync (device auth)

class DeviceIn(BaseModel):
    branch_id: int
    name: str = Field(min_length=2, max_length=60)


@router.get("/sync/devices")
def devices(request: Request):
    return MotionJSON(unit_of_work(request, lambda p: sync.sync_status(p)))


@router.post("/sync/devices")
def device_register(body: DeviceIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: sync.register_device(p, branch_id=body.branch_id, name=body.name)),
                      status_code=201)


@router.post("/sync/devices/{device_id}/revoke")
def device_revoke(device_id: int, body: CancelIn, request: Request):
    unit_of_work(request, lambda p: sync.revoke_device(p, device_id, body.reason))
    return MotionJSON({"ok": True})


def _device_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    return auth[7:] if auth.lower().startswith("bearer ") else None


@router.get("/edge/snapshot")
def edge_snapshot(request: Request):
    token = _device_token(request)
    return MotionJSON(t.run(lambda: sync.snapshot(sync.authenticate_device(token)), user=0))


class EdgeEventsIn(BaseModel):
    events: list[dict] = Field(min_length=1, max_length=200)


@router.post("/edge/events")
def edge_events(body: EdgeEventsIn, request: Request):
    token = _device_token(request)
    device = t.run(lambda: sync.authenticate_device(token), user=0)
    acks = []
    for ev in body.events:  # one transaction per event: a bad event cannot block the rest
        try:
            acks.append(t.run(lambda ev=ev: sync.ingest_event(device, ev), user=0))
        except AppError as exc:
            acks.append({"event_id": ev.get("event_id"), "status": "rejected", "error": exc.message})
        except t.EngineError as exc:
            acks.append({"event_id": ev.get("event_id"), "status": "rejected", "error": str(exc)})
    return MotionJSON({"acks": acks})
