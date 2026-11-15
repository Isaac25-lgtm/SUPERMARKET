"""Catalog and point-of-sale endpoints."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..services import catalog, common, pos
from ..web import MotionJSON, unit_of_work

router = APIRouter(prefix="/api")


@router.get("/catalog/products")
def products(request: Request, branch_id: int | None = None, q: str | None = None, category: int | None = None,
             low_stock: bool = False, include_inactive: bool = False):
    return MotionJSON(unit_of_work(request, lambda p: catalog.list_products(
        p, branch_id=branch_id, q=q, category=category, low_stock=low_stock, include_inactive=include_inactive)))


@router.get("/catalog/products/{product_id}")
def product(product_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: catalog.product(p, product_id)))


@router.get("/catalog/categories")
def categories(request: Request):
    return MotionJSON({"categories": unit_of_work(request, lambda p: catalog.categories(p))})


@router.get("/catalog/lookup")
def lookup(request: Request, code: str, branch_id: int | None = None):
    return MotionJSON(unit_of_work(request, lambda p: catalog.lookup(p, code, branch_id)))


class ProductIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category_id: int
    shelf_price: Decimal = Field(gt=0)
    cost_price: Decimal = Field(ge=0)
    sku: str | None = None
    barcode: str | None = None
    sold_by_weight: bool = False
    plu: str | None = None
    perishable: bool = False
    shelf_life_days: int | None = None
    reorder_point: Decimal = Decimal(0)
    reorder_qty: Decimal = Decimal(0)
    preferred_supplier: int | None = None


@router.post("/catalog/products")
def create_product(body: ProductIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: catalog.create_product(p, **body.model_dump())), status_code=201)


class ProductUpdateIn(BaseModel):
    name: str | None = None
    category_id: int | None = None
    barcode: str | None = None
    reorder_point: Decimal | None = None
    reorder_qty: Decimal | None = None
    perishable: bool | None = None
    shelf_life_days: int | None = None
    preferred_supplier: int | None = None
    active: bool | None = None


@router.patch("/catalog/products/{product_id}")
def update_product(product_id: int, body: ProductUpdateIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: catalog.update_product(p, product_id, **body.model_dump())))


class PriceIn(BaseModel):
    new_price: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=300)


class BarcodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


@router.post("/catalog/products/{product_id}/barcodes")
def add_barcode(product_id: int, body: BarcodeIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: catalog.add_barcode(p, product_id, body.code)), status_code=201)


@router.post("/catalog/products/{product_id}/barcodes/remove")
def remove_barcode(product_id: int, body: BarcodeIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: catalog.remove_barcode(p, product_id, body.code)))


@router.post("/catalog/products/{product_id}/price")
def change_price(product_id: int, body: PriceIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: catalog.change_price(p, product_id, new_price=body.new_price,
                                                                             reason=body.reason)))


# ------------------------------------------------------------------ POS

@router.get("/pos/registers/{register_id}/catalog")
def till_catalog(register_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.till_catalog(p, register_id)))


@router.get("/pos/shift")
def my_shift(request: Request):
    return MotionJSON({"shift": unit_of_work(request, lambda p: pos.my_open_shift(p))})


class OpenShiftIn(BaseModel):
    register_id: int
    opening_float: Decimal = Field(ge=0)


@router.post("/pos/shifts")
def open_shift(body: OpenShiftIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.open_shift(p, register_id=body.register_id,
                                                                       opening_float=body.opening_float)), status_code=201)


class CloseShiftIn(BaseModel):
    counted_cash: Decimal = Field(ge=0)
    cash_drop: Decimal = Field(default=Decimal(0), ge=0)
    note: str | None = None


@router.post("/pos/shifts/{shift_id}/close")
def close_shift(shift_id: int, body: CloseShiftIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.close_shift(p, shift_id, counted_cash=body.counted_cash,
                                                                        cash_drop=body.cash_drop, note=body.note)))


class NoteIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/pos/shifts/{shift_id}/reconcile")
def reconcile_shift(shift_id: int, body: NoteIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.reconcile_shift(p, shift_id, body.note)))


@router.get("/pos/shifts")
def shifts(request: Request, branch_id: int | None = None, status: str | None = None):
    return MotionJSON({"shifts": unit_of_work(request, lambda p: pos.list_shifts(p, branch_id=branch_id, status=status))})


class LineIn(BaseModel):
    product_id: int
    quantity: Decimal = Field(gt=0)
    discount_pct: Decimal = Field(default=Decimal(0), ge=0, le=100)


class PaymentIn(BaseModel):
    method: str
    amount: Decimal = Field(gt=0)
    reference: str | None = None


class CheckoutIn(BaseModel):
    register_id: int
    idempotency_key: str = Field(min_length=8, max_length=80)
    lines: list[LineIn] = Field(min_length=1, max_length=200)
    payments: list[PaymentIn] = Field(min_length=1, max_length=6)
    override_approval_id: int | None = None


@router.post("/pos/sales")
def checkout(body: CheckoutIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.checkout(
        p, register_id=body.register_id, idempotency_key=body.idempotency_key,
        lines=[ln.model_dump() for ln in body.lines], payments=[x.model_dump() for x in body.payments],
        override_approval_id=body.override_approval_id)), status_code=201)


class OverrideIn(BaseModel):
    register_id: int
    lines: list[LineIn]
    manager_username: str
    manager_password: str
    reason: str = Field(min_length=3, max_length=300)


@router.post("/pos/overrides")
def override(body: OverrideIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.request_override(
        p, register_id=body.register_id, lines=[ln.model_dump() for ln in body.lines],
        manager_username=body.manager_username, manager_password=body.manager_password, reason=body.reason)))


@router.get("/pos/sales")
def sales(request: Request, branch_id: int | None = None, date_from: dt.date | None = None,
          date_to: dt.date | None = None, cashier_id: int | None = None):
    def fn(p):
        today = common.today()
        return {"sales": pos.list_sales(p, branch_id=branch_id, date_from=date_from or today, date_to=date_to or today,
                                        cashier_id=cashier_id)}
    return MotionJSON(unit_of_work(request, fn))


@router.get("/pos/sales/{sale_id}")
def sale(sale_id: int, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.receipt(p, sale_id)))


@router.get("/pos/receipts/{number}")
def find_receipt(number: str, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.find_sale(p, number)))


class RefundLineIn(BaseModel):
    line_id: int
    quantity: Decimal = Field(gt=0)


class RefundRequestIn(BaseModel):
    sale_id: int
    lines: list[RefundLineIn] = Field(min_length=1)
    reason: str = Field(min_length=5, max_length=500)


@router.post("/pos/refunds/requests")
def refund_request(body: RefundRequestIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.request_refund(
        p, sale_id=body.sale_id, lines=[ln.model_dump() for ln in body.lines], reason=body.reason)), status_code=201)


class RefundExecuteIn(BaseModel):
    approval_id: int
    register_id: int
    idempotency_key: str = Field(min_length=8, max_length=80)


@router.post("/pos/refunds")
def refund_execute(body: RefundExecuteIn, request: Request):
    return MotionJSON(unit_of_work(request, lambda p: pos.execute_refund(
        p, approval_id=body.approval_id, register_id=body.register_id, idempotency_key=body.idempotency_key)),
        status_code=201)
