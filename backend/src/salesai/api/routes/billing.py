"""Billing API: plans (public), the owner's subscription, usage, invoices and payment."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from salesai.api.deps import RT, Owner, tx
from salesai.modules.billing import catalogue
from salesai.modules.catalog import repo

router = APIRouter(tags=["billing"])


class SubscribeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str
    interval: Literal["month", "year"] = "month"


class InvoiceLine(BaseModel):
    description: str
    quantity: int
    unit_paise: int
    amount_paise: int


class InvoiceOut(BaseModel):
    id: uuid.UUID
    number: str
    status: Literal["open", "paid", "void"]
    plan: str
    billing_interval: str
    period_start: datetime
    period_end: datetime
    lines: list[InvoiceLine]
    subtotal_paise: int
    gst_paise: int
    total_paise: int
    currency: str
    issued_at: datetime
    due_at: datetime
    paid_at: datetime | None


class PaymentOutcome(BaseModel):
    status: Literal["succeeded", "pending", "failed"]
    mode: str
    instructions: str | None = None


class PaidInvoice(InvoiceOut):
    payment: PaymentOutcome


class SubscribeOut(BaseModel):
    scheduled: bool
    invoice: InvoiceOut | None


class Usage(BaseModel):
    conversations: int
    products: int
    numbers: int
    team: int
    included_conversations: int | None
    extra_conversations: int
    overage_estimate_paise: int
    period_start: datetime | None
    period_end: datetime | None


class OpenInvoice(BaseModel):
    id: uuid.UUID
    number: str
    total_paise: int
    due_at: datetime


class BillingOut(BaseModel):
    status: str
    state: str
    plan: str
    plan_name: str
    interval: str
    trial_ends_at: datetime | None
    trial_days_left: int | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    pending_plan: str | None
    pending_interval: str | None
    usage: Usage
    limits: dict[str, int | None]
    open_invoice: OpenInvoice | None
    assistant_paused_by_billing: bool
    payment_mode: str


@router.get("/plans", summary="Plans and prices (public)")
async def plans() -> dict[str, Any]:
    return catalogue()


@router.get("/billing", response_model=BillingOut, summary="The subscription, usage and what is due")
async def billing(rt: RT, p: Owner) -> Any:
    return await rt.billing.view(p.bid)


@router.post("/billing/subscribe", response_model=SubscribeOut, summary="Choose a plan: upgrades bill now, downgrades apply at the end of the period")
async def subscribe(body: SubscribeIn, rt: RT, p: Owner) -> Any:
    res = await rt.billing.subscribe(p.bid, body.plan, body.interval)
    async with tx(rt, p) as c:
        await repo.audit(c, p.bid, p.account_id, "owner", "billing.plan_chosen", "subscription", p.bid.hex, {"plan": body.plan, "interval": body.interval, "scheduled": res["scheduled"]})
    return res


@router.post("/billing/cancel", response_model=BillingOut, summary="Cancel at the end of the paid period (or end the trial now)")
async def cancel(rt: RT, p: Owner) -> Any:
    out = await rt.billing.cancel(p.bid)
    async with tx(rt, p) as c:
        await repo.audit(c, p.bid, p.account_id, "owner", "billing.cancelled", "subscription", p.bid.hex)
    return out


@router.post("/billing/resume", response_model=BillingOut, summary="Keep the subscription after all")
async def resume(rt: RT, p: Owner) -> Any:
    return await rt.billing.resume(p.bid)


@router.get("/billing/invoices", response_model=list[InvoiceOut])
async def invoices(rt: RT, p: Owner) -> Any:
    return await rt.billing.invoices(p.bid)


@router.post("/billing/invoices/{invoice_id}/pay", response_model=PaidInvoice, summary="Pay an open invoice")
async def pay(invoice_id: uuid.UUID, rt: RT, p: Owner) -> Any:
    out = await rt.billing.pay(p.bid, invoice_id)
    if out["status"] == "paid":
        async with tx(rt, p) as c:
            await repo.audit(c, p.bid, p.account_id, "owner", "billing.paid", "invoice", str(invoice_id), {"number": out["number"]})
    return out


@router.get("/billing/invoices/{invoice_id}/document", response_class=HTMLResponse, summary="A printable tax invoice")
async def document(invoice_id: uuid.UUID, rt: RT, p: Owner) -> Any:
    return HTMLResponse(await rt.billing.invoice_html(p.bid, invoice_id))

