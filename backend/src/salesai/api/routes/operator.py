from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Operator
from salesai.api.errors import ApiError
from salesai.config import ALL_QUEUES
from salesai.db import jsonb
from salesai.events.outbox import emit
from salesai.modules.tenants import (
    add_cloud_number,
    add_simulated_number,
    create_business,
    delete_tenant,
    export_tenant,
)
from salesai.phone import normalize_phone

router = APIRouter(prefix="/operator", tags=["operator"])


class NumberStat(BaseModel):
    display_phone: str
    channel: str
    status: str
    quality: str | None


class TenantRow(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    ai_enabled: bool
    plan: str
    created_at: datetime
    owner_phone: str | None
    numbers: list[NumberStat]
    inbound_24h: int
    turns_24h: int
    failed_24h: int
    open_handoffs: int
    cost_inr_24h: float


class NewBusiness(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    owner_name: str = Field(min_length=1, max_length=80)
    owner_phone: str = Field(min_length=7, max_length=20)
    timezone: str = "Asia/Kolkata"
    plan: str = "pilot"
    language: Literal["en", "hi"] = "en"
    simulated_number: str | None = Field(default=None, description="Create a simulated business WhatsApp number (development / demos).")
    ai_enabled: bool = True


class CloudNumberIn(BaseModel):
    phone_number_id: str
    waba_id: str
    display_phone: str
    access_token: str = Field(repr=False)
    coexistence: bool = False
    verified_name: str | None = None


class TenantPatch(BaseModel):
    status: Literal["onboarding", "active", "paused", "churned"] | None = None
    plan: str | None = Field(default=None, max_length=40)
    limits: dict[str, int] | None = None
    ai_enabled: bool | None = None


class AlertOut(BaseModel):
    id: int
    business_id: uuid.UUID | None
    business_name: str | None
    severity: str
    kind: str
    message: str
    created_at: datetime
    resolved_at: datetime | None


@router.get("/businesses", response_model=list[TenantRow], summary="Tenants with connection status, quality, AI status, volume and error rates (FR-OP-1)")
async def businesses(rt: RT, p: Operator) -> Any:
    async with rt.db.system_tx() as c:
        rows = await (await c.execute(
            """SELECT b.id, b.name, b.status, b.ai_enabled, b.plan, b.created_at,
                 (SELECT a.phone FROM business_users bu JOIN accounts a ON a.id=bu.account_id WHERE bu.business_id=b.id AND bu.role='owner' ORDER BY bu.created_at LIMIT 1) AS owner_phone,
                 COALESCE((SELECT json_agg(json_build_object('display_phone', n.display_phone, 'channel', n.channel, 'status', n.status, 'quality', n.quality_rating)) FROM whatsapp_numbers n WHERE n.business_id=b.id), '[]'::json) AS numbers,
                 (SELECT count(*) FROM messages m WHERE m.business_id=b.id AND m.direction='in' AND m.created_at > now() - interval '24 hours') AS inbound_24h,
                 (SELECT count(*) FROM turns t WHERE t.business_id=b.id AND t.created_at > now() - interval '24 hours') AS turns_24h,
                 (SELECT count(*) FROM messages m WHERE m.business_id=b.id AND m.direction='out' AND m.status='failed' AND m.created_at > now() - interval '24 hours') AS failed_24h,
                 (SELECT count(*) FROM handoffs h WHERE h.business_id=b.id AND h.status='open') AS open_handoffs,
                 (SELECT COALESCE(sum(cost_micros),0) FROM turns t WHERE t.business_id=b.id AND t.created_at > now() - interval '24 hours') AS cost_micros
               FROM businesses b ORDER BY b.created_at DESC""")).fetchall()
    return [TenantRow(**{**{k: r[k] for k in ("id", "name", "status", "ai_enabled", "plan", "created_at", "owner_phone", "numbers")}, "inbound_24h": int(r["inbound_24h"]),
                         "turns_24h": int(r["turns_24h"]), "failed_24h": int(r["failed_24h"]), "open_handoffs": int(r["open_handoffs"]),
                         "cost_inr_24h": round(int(r["cost_micros"]) / 1_000_000, 4)}) for r in rows]


@router.post("/businesses", response_model=TenantRow, status_code=201, summary="Onboard a business (FR-ON-1)")
async def create(body: NewBusiness, rt: RT, p: Operator) -> Any:
    try:
        owner = normalize_phone(body.owner_phone)
    except ValueError as e:
        raise ApiError(422, "invalid_phone", "Enter a valid mobile number with country code.") from e
    cb = await create_business(rt.db, rt.settings.master_key_bytes, name=body.name, owner_phone=owner, owner_name=body.owner_name, timezone=body.timezone,
                               plan=body.plan, language=body.language, ai_enabled=body.ai_enabled)
    if body.simulated_number:
        if not rt.settings.simulator_enabled:
            raise ApiError(409, "simulator_disabled", "The simulator is disabled in this environment.")
        await add_simulated_number(rt.db, cb.business_id, body.simulated_number, verified_name=body.name)
    rows = [r for r in await businesses(rt, p) if r.id == cb.business_id]
    return rows[0]


@router.post("/businesses/{business_id}/numbers", status_code=201, summary="Register a WhatsApp Cloud API number manually for a pilot (FR-ON-3)")
async def add_number(business_id: uuid.UUID, body: CloudNumberIn, rt: RT, p: Operator) -> dict[str, str]:
    nid = await add_cloud_number(rt.db, rt.vault, business_id, phone_number_id=body.phone_number_id, waba_id=body.waba_id,
                                 display_phone=body.display_phone, access_token=body.access_token, coexistence=body.coexistence, verified_name=body.verified_name)
    return {"id": str(nid)}


@router.patch("/businesses/{business_id}", status_code=204)
async def patch_business(business_id: uuid.UUID, body: TenantPatch, rt: RT, p: Operator) -> None:
    d = body.model_dump(exclude_unset=True)
    async with rt.db.tenant(business_id) as c:
        for k, v in d.items():
            if v is None:
                continue
            await c.execute(f"UPDATE businesses SET {k}=%s WHERE id=%s", (jsonb(v) if k == "limits" else v, business_id))  # noqa: S608
        await c.execute("INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity, detail) VALUES (%s,%s,'operator','operator.updated','business',%s)",
                        (business_id, p.account_id, jsonb({"fields": sorted(d)})))


@router.post("/businesses/{business_id}/impersonate", summary="Short-lived owner access for support (audited)")
async def impersonate(business_id: uuid.UUID, rt: RT, p: Operator) -> dict[str, Any]:
    pair = await rt.auth.impersonate(p, business_id, None)
    return {"access_token": pair.access_token, "refresh_token": pair.refresh_token, "expires_in": pair.expires_in, "business_id": str(business_id), "role": "owner"}


@router.get("/businesses/{business_id}/export", summary="Per-tenant data export (no floors, tokens or secrets)")
async def export(business_id: uuid.UUID, rt: RT, p: Operator) -> dict[str, Any]:
    return await export_tenant(rt.db, business_id)


@router.delete("/businesses/{business_id}", summary="Hard-delete a tenant and every trace of its data (DPDP)")
async def delete(business_id: uuid.UUID, rt: RT, p: Operator, confirm_name: Annotated[str, Query(min_length=1)]) -> dict[str, Any]:
    async with rt.db.system_tx() as c:
        b = await (await c.execute("SELECT name FROM businesses WHERE id=%s", (business_id,))).fetchone()
    if b is None:
        raise ApiError(404, "not_found", "Business not found.")
    if b["name"] != confirm_name:
        raise ApiError(422, "confirmation_mismatch", "Type the business name exactly to confirm deletion.")
    return {"deleted": await delete_tenant(rt.db, business_id, rt.queue)}


@router.get("/alerts", response_model=list[AlertOut])
async def alerts(rt: RT, p: Operator, resolved: bool = False, limit: Annotated[int, Query(ge=1, le=200)] = 100) -> Any:
    async with rt.db.system_tx() as c:
        return await (await c.execute(
            """SELECT a.*, b.name AS business_name FROM operator_alerts a LEFT JOIN businesses b ON b.id=a.business_id
               WHERE (a.resolved_at IS NOT NULL) = %s ORDER BY a.created_at DESC LIMIT %s""", (resolved, limit))).fetchall()


@router.post("/alerts/{alert_id}/resolve", status_code=204)
async def resolve_alert(alert_id: int, rt: RT, p: Operator) -> None:
    async with rt.db.system_tx() as c:
        await c.execute("UPDATE operator_alerts SET resolved_at=now() WHERE id=%s", (alert_id,))


class QueueOut(BaseModel):
    queue: str
    pending: int
    due: int
    running: int
    dead: int
    oldest_due_age_s: float


class DeadOut(BaseModel):
    id: int
    kind: str
    business_id: uuid.UUID | None
    attempts: int
    last_error: str | None
    finished_at: datetime | None


@router.get("/queues", response_model=list[QueueOut], summary="Depth, age and failure counts per queue")
async def queues(rt: RT, p: Operator) -> Any:
    return [s.__dict__ for s in await rt.queue.stats(list(ALL_QUEUES))]


@router.get("/queues/{queue}/dead", response_model=list[DeadOut], summary="Dead letters with their error")
async def dead(queue: str, rt: RT, p: Operator) -> Any:
    if queue not in ALL_QUEUES:
        raise ApiError(404, "not_found", "Unknown queue.")
    return [{**d, "id": int(d["id"])} for d in await rt.queue.dead_letters(queue)]


@router.post("/queues/{queue}/dead/{job_id}/replay", status_code=204)
async def replay(queue: str, job_id: int, rt: RT, p: Operator) -> None:
    if not await rt.queue.replay_dead(queue, str(job_id)):
        raise ApiError(404, "not_found", "No such dead letter.")


class WebhookOut(BaseModel):
    id: int
    received_at: datetime
    processed_at: datetime | None
    error: str | None
    summary: str


@router.get("/webhooks", response_model=list[WebhookOut], summary="Raw webhook log (FR-OP-3)")
async def webhooks(rt: RT, p: Operator, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> Any:
    async with rt.db.system_tx() as c:
        rows = await (await c.execute("SELECT id, received_at, processed_at, error, payload FROM webhook_events ORDER BY id DESC LIMIT %s", (limit,))).fetchall()
    out = []
    for r in rows:
        ch = (r["payload"].get("entry") or [{}])[0].get("changes") or [{}]
        v = ch[0].get("value", {})
        kinds = [k for k in ("messages", "statuses", "message_echoes") if v.get(k)] or [ch[0].get("field", "?")]
        out.append({"id": r["id"], "received_at": r["received_at"], "processed_at": r["processed_at"], "error": r["error"], "summary": ", ".join(kinds)})
    return out


@router.post("/webhooks/{webhook_id}/replay", status_code=202, summary="Re-process a stored webhook (idempotent: duplicates are ignored)")
async def replay_webhook(webhook_id: int, rt: RT, p: Operator) -> None:
    async with rt.db.system_tx() as c:
        if await (await c.execute("SELECT 1 FROM webhook_events WHERE id=%s", (webhook_id,))).fetchone() is None:
            raise ApiError(404, "not_found", "No such webhook.")
        await emit(c, "webhook.received", {"webhook_event_id": webhook_id}, business_id=None)


class TurnRow(BaseModel):
    id: uuid.UUID
    business_name: str
    created_at: datetime
    intent: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_inr: float
    latency_ms: int | None
    checks_failed: int


@router.get("/turns", response_model=list[TurnRow], summary="Recent AI turns: decision metadata, model, tokens, cost, latency (FR-OP-2) — no customer content")
async def turns(rt: RT, p: Operator, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> Any:
    async with rt.db.system_tx() as c:
        rows = await (await c.execute(
            """SELECT t.id, b.name AS business_name, t.created_at, t.decision->>'intent' AS intent, t.model, t.input_tokens, t.output_tokens, t.cost_micros, t.latency_ms,
                      CASE WHEN jsonb_typeof(t.decision->'checks'->'attempts') = 'array' THEN jsonb_array_length(t.decision->'checks'->'attempts') ELSE 0 END AS attempts
               FROM turns t JOIN businesses b ON b.id=t.business_id ORDER BY t.created_at DESC LIMIT %s""", (limit,))).fetchall()
    return [TurnRow(id=r["id"], business_name=r["business_name"], created_at=r["created_at"], intent=r["intent"], model=r["model"], input_tokens=r["input_tokens"],
                    output_tokens=r["output_tokens"], cost_inr=round((r["cost_micros"] or 0) / 1_000_000, 4), latency_ms=r["latency_ms"], checks_failed=max(0, int(r["attempts"]) - 1)) for r in rows]


class CostRow(BaseModel):
    business_id: uuid.UUID
    business_name: str
    conversations: int
    turns: int
    tokens: int
    cost_inr: float
    cost_per_conversation_inr: float | None


@router.get("/costs", response_model=list[CostRow], summary="LLM cost per tenant and per conversation (NFR-14)")
async def costs(rt: RT, p: Operator, days: Annotated[int, Query(ge=1, le=90)] = 30) -> Any:
    async with rt.db.system_tx() as c:
        rows = await (await c.execute(
            """SELECT b.id, b.name, count(DISTINCT t.conversation_id) AS conversations, count(t.id) AS turns,
                      COALESCE(sum(COALESCE(t.input_tokens,0)+COALESCE(t.output_tokens,0)),0) AS tokens, COALESCE(sum(t.cost_micros),0) AS micros
               FROM businesses b LEFT JOIN turns t ON t.business_id=b.id AND t.created_at > now() - make_interval(days => %s) GROUP BY b.id, b.name ORDER BY micros DESC""", (days,))).fetchall()
    return [CostRow(business_id=r["id"], business_name=r["name"], conversations=int(r["conversations"]), turns=int(r["turns"]), tokens=int(r["tokens"]),
                    cost_inr=round(int(r["micros"]) / 1e6, 4), cost_per_conversation_inr=round(int(r["micros"]) / 1e6 / int(r["conversations"]), 4) if r["conversations"] else None) for r in rows]


@router.get("/billing", summary="Platform billing summary: subscriptions by status, MRR, collections, outstanding")
async def billing_summary(rt: RT, p: Operator) -> dict[str, Any]:
    return await rt.billing.platform_summary()


@router.get("/invoices", summary="Open invoices across the platform (to reconcile manual payments)")
async def open_invoices(rt: RT, p: Operator) -> list[dict[str, Any]]:
    async with rt.db.system_tx() as c:
        rows = await (await c.execute(
            """SELECT i.id, i.number, i.status, i.total_paise, i.issued_at, i.due_at, b.name AS business_name, i.plan
               FROM invoices i JOIN businesses b ON b.id = i.business_id WHERE i.status='open' ORDER BY i.due_at LIMIT 200""")).fetchall()
    return [dict(r) for r in rows]


@router.post("/invoices/{invoice_id}/mark-paid", summary="Confirm a bank transfer / UPI payment for an invoice")
async def mark_paid(invoice_id: uuid.UUID, rt: RT, p: Operator, reference: Annotated[str | None, Query(max_length=80)] = None) -> dict[str, Any]:
    inv = await rt.billing.settle_manually(invoice_id, reference)
    return {"id": str(inv["id"]), "number": inv["number"], "status": inv["status"]}
