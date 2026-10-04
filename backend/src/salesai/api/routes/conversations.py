from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Tenant, decode_cursor, encode_cursor, tx
from salesai.api.errors import ApiError
from salesai.db import required
from salesai.events.outbox import emit
from salesai.rules import window_closes_at, window_open

router = APIRouter(tags=["conversations"])
Stage = Literal["new", "exploring", "interested", "negotiating", "ready_to_buy", "won", "lost"]


class CustomerBrief(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str
    is_personal: bool
    opted_out: bool


class LastMessage(BaseModel):
    body: str | None
    direction: Literal["in", "out"]
    sender: str
    kind: str
    at: datetime


class ConversationSummary(BaseModel):
    id: uuid.UUID
    customer: CustomerBrief
    number_id: uuid.UUID
    lead_stage: Stage
    state: str
    last_message: LastMessage | None
    unanswered: int
    open_handoff: str | None
    ai_paused_until: datetime | None
    ai_paused_reason: str | None
    selling_stopped: bool
    window_open: bool
    last_inbound_at: datetime | None
    updated_at: datetime


class ConversationList(BaseModel):
    items: list[ConversationSummary]
    next_cursor: str | None


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: Literal["in", "out"]
    sender: Literal["customer", "ai", "owner", "system"]
    kind: str
    body: str | None
    status: str
    error: str | None
    created_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None
    turn_id: uuid.UUID | None


class NegotiationOut(BaseModel):
    id: uuid.UUID
    product: str
    variant: str
    status: str
    quantity: int
    current_offer: str | None
    customer_best: str | None
    step: int
    history: list[dict[str, Any]]


class DealBrief(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    value: str | None
    items: list[dict[str, Any]]
    details: dict[str, Any]
    created_at: datetime


class HandoffBrief(BaseModel):
    id: uuid.UUID
    reason: str
    note: str | None
    status: str
    created_at: datetime


class ConversationDetail(ConversationSummary):
    qualification: dict[str, Any]
    summary: str | None
    window_closes_at: datetime | None
    messages: list[MessageOut]
    negotiations: list[NegotiationOut]
    deals: list[DealBrief]
    handoffs: list[HandoffBrief]
    version: int


class MessagePage(BaseModel):
    items: list[MessageOut]
    next_cursor: str | None


class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


class PauseIn(BaseModel):
    minutes: int = Field(default=120, ge=1, le=10080)


class ConversationPatch(BaseModel):
    lead_stage: Literal["lost", "interested", "exploring"] | None = None
    selling_stopped: bool | None = None
    lost_reason: str | None = Field(default=None, max_length=200)


_SELECT = """
SELECT cv.*, cu.name AS cname, cu.wa_id, cu.is_personal, cu.opted_out,
  (SELECT json_build_object('body', m.body, 'direction', m.direction, 'sender', m.sender, 'kind', m.kind, 'at', m.created_at)
     FROM messages m WHERE m.conversation_id = cv.id ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_message,
  (SELECT count(*) FROM messages m WHERE m.conversation_id = cv.id AND m.direction='in' AND NOT m.answered) AS unanswered,
  (SELECT h.reason FROM handoffs h WHERE h.conversation_id = cv.id AND h.status='open' ORDER BY h.created_at DESC LIMIT 1) AS open_handoff
FROM conversations cv JOIN customers cu ON cu.id = cv.customer_id
"""


def _summary(r: dict[str, Any]) -> ConversationSummary:
    lm = r["last_message"]
    return ConversationSummary(
        id=r["id"], customer=CustomerBrief(id=r["customer_id"], name=r["cname"], phone="+" + r["wa_id"], is_personal=r["is_personal"], opted_out=r["opted_out"]),
        number_id=r["whatsapp_number_id"], lead_stage=r["lead_stage"], state=r["state"],
        last_message=LastMessage(body=lm["body"], direction=lm["direction"], sender=lm["sender"], kind=lm["kind"], at=datetime.fromisoformat(lm["at"])) if lm else None,
        unanswered=r["unanswered"], open_handoff=r["open_handoff"], ai_paused_until=r["ai_paused_until"], ai_paused_reason=r["ai_paused_reason"],
        selling_stopped=r["selling_stopped"], window_open=window_open(r["last_inbound_at"]), last_inbound_at=r["last_inbound_at"], updated_at=r["updated_at"])


@router.get("/conversations", response_model=ConversationList)
async def list_conversations(rt: RT, p: Tenant, stage: Annotated[list[Stage] | None, Query()] = None, attention: bool | None = None,
                             paused: bool | None = None, search: str | None = None, number_id: uuid.UUID | None = None,
                             limit: Annotated[int, Query(ge=1, le=100)] = 30, cursor: str | None = None) -> Any:
    where: list[str] = ["TRUE"]
    args: list[Any] = []
    if stage:
        where.append("cv.lead_stage = ANY(%s)")
        args.append(list(stage))
    if number_id:
        where.append("cv.whatsapp_number_id = %s")
        args.append(number_id)
    if search:
        where.append("(cu.name ILIKE %s OR cu.wa_id LIKE %s)")
        args += [f"%{search}%", f"%{search.lstrip('+')}%"]
    if attention:
        where.append("(EXISTS (SELECT 1 FROM handoffs h WHERE h.conversation_id = cv.id AND h.status='open'))")
    if paused is not None:
        where.append("(cv.ai_paused_until IS NOT NULL AND cv.ai_paused_until > now()) = %s")
        args.append(paused)
    cur = decode_cursor(cursor)
    if cur:
        where.append("(cv.updated_at, cv.id::text) < (%s, %s)")
        args += [cur[0], cur[1]]
    async with tx(rt, p) as c:
        rows = await (await c.execute(f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY cv.updated_at DESC, cv.id::text DESC LIMIT %s", (*args, limit + 1))).fetchall()  # noqa: S608
    more = len(rows) > limit
    rows = rows[:limit]
    return ConversationList(items=[_summary(r) for r in rows], next_cursor=encode_cursor(rows[-1]["updated_at"], rows[-1]["id"]) if more and rows else None)


def _msg(r: dict[str, Any]) -> MessageOut:
    return MessageOut(**{k: r[k] for k in ("id", "direction", "sender", "kind", "body", "status", "error", "created_at", "sent_at", "delivered_at", "read_at", "turn_id")})


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        r = await (await c.execute(f"{_SELECT} WHERE cv.id=%s", (conversation_id,))).fetchone()  # noqa: S608
        if r is None:
            raise ApiError(404, "not_found", "Conversation not found.")
        msgs = list(reversed(await (await c.execute("SELECT * FROM messages WHERE conversation_id=%s ORDER BY created_at DESC, id DESC LIMIT 100", (conversation_id,))).fetchall()))
        negs = await (await c.execute(
            """SELECT n.*, p.name AS pname, v.name AS vname FROM negotiations n JOIN product_variants v ON v.id=n.variant_id JOIN products p ON p.id=v.product_id
               WHERE n.conversation_id=%s ORDER BY n.created_at DESC""", (conversation_id,))).fetchall()
        deals = await (await c.execute("SELECT * FROM deals WHERE conversation_id=%s ORDER BY created_at DESC", (conversation_id,))).fetchall()
        hos = await (await c.execute("SELECT * FROM handoffs WHERE conversation_id=%s ORDER BY created_at DESC", (conversation_id,))).fetchall()
    base = _summary(r).model_dump()
    return ConversationDetail(
        **base, qualification={k: v for k, v in (r["qualification"] or {}).items() if k not in ("pending_order",)}, summary=r["summary"],
        window_closes_at=window_closes_at(r["last_inbound_at"]), version=r["version"], messages=[_msg(m) for m in msgs],
        negotiations=[NegotiationOut(id=n["id"], product=n["pname"], variant=n["vname"], status=n["status"], quantity=n["quantity"],
                                     current_offer=str(n["current_offer"]) if n["current_offer"] is not None else None,
                                     customer_best=str(n["customer_best"]) if n["customer_best"] is not None else None, step=n["step"], history=n["history"]) for n in negs],
        deals=[DealBrief(id=d["id"], kind=d["kind"], status=d["status"], value=str(d["value"]) if d["value"] is not None else None, items=d["items"], details=d["details"], created_at=d["created_at"]) for d in deals],
        handoffs=[HandoffBrief(id=h["id"], reason=h["reason"], note=h["note"], status=h["status"], created_at=h["created_at"]) for h in hos])


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
async def conversation_messages(conversation_id: uuid.UUID, rt: RT, p: Tenant, limit: Annotated[int, Query(ge=1, le=200)] = 50, before: str | None = None) -> Any:
    cur = decode_cursor(before)
    async with tx(rt, p) as c:
        rows = await (await c.execute(
            """SELECT * FROM messages WHERE conversation_id=%s AND (%s::timestamptz IS NULL OR (created_at, id::text) < (%s, %s))
               ORDER BY created_at DESC, id::text DESC LIMIT %s""", (conversation_id, cur[0] if cur else None, cur[0] if cur else None, cur[1] if cur else None, limit + 1))).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return MessagePage(items=[_msg(m) for m in reversed(rows)], next_cursor=encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if more and rows else None)


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut, status_code=201,
             summary="Reply as a person from the dashboard. Pauses the AI in this conversation; only inside the 24-hour window.")
async def send_message(conversation_id: uuid.UUID, body: SendIn, rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s FOR UPDATE", (conversation_id,))).fetchone()
        if conv is None:
            raise ApiError(404, "not_found", "Conversation not found.")
        if not window_open(conv["last_inbound_at"]):
            raise ApiError(409, "window_closed", "WhatsApp only allows free-form replies within 24 hours of the customer's last message.")
        biz = await (await c.execute("SELECT conversation_settings FROM businesses WHERE id=%s", (p.bid,))).fetchone()
        pause = timedelta(minutes=int((biz["conversation_settings"] or {}).get("owner_pause_minutes", 120)))
        m = required(await (await c.execute(
            "INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status, answered) VALUES (%s,%s,'out','owner','text',%s,'queued',true) RETURNING *",
            (p.bid, conversation_id, body.text.strip()))).fetchone(), "message")
        v = (await (await c.execute(
            """UPDATE conversations SET version = version + 1, state='idle', ai_paused_until = now() + %s, ai_paused_reason='owner_reply', last_owner_reply_at = now()
               WHERE id=%s RETURNING version""", (pause, conversation_id))).fetchone())["version"]
        await c.execute("UPDATE messages SET status='cancelled', error='superseded' WHERE conversation_id=%s AND direction='out' AND sender='ai' AND status='queued'", (conversation_id,))
        await c.execute("UPDATE messages SET answered=true WHERE conversation_id=%s AND direction='in' AND NOT answered", (conversation_id,))
        await emit(c, "outbound.action_requested", {"conversation_id": conversation_id, "action": "send_text", "message_id": m["id"], "version": v, "human": True, "last_part": True},
                   business_id=p.bid, ordering_key=f"conv:{conversation_id}")
    await rt.queue.cancel_superseded(f"conversation:{conversation_id}", v)
    return _msg(m)


@router.post("/conversations/{conversation_id}/pause", response_model=ConversationSummary)
async def pause(conversation_id: uuid.UUID, body: PauseIn, rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        r = await c.execute("UPDATE conversations SET ai_paused_until = now() + %s, ai_paused_reason='manual' WHERE id=%s", (timedelta(minutes=body.minutes), conversation_id))
        if r.rowcount == 0:
            raise ApiError(404, "not_found", "Conversation not found.")
        row = required(await (await c.execute(f"{_SELECT} WHERE cv.id=%s", (conversation_id,))).fetchone(), "conversation")  # noqa: S608
    return _summary(row)


@router.post("/conversations/{conversation_id}/resume", response_model=ConversationSummary)
async def resume(conversation_id: uuid.UUID, rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        r = await c.execute("UPDATE conversations SET ai_paused_until=NULL, ai_paused_reason=NULL WHERE id=%s", (conversation_id,))
        if r.rowcount == 0:
            raise ApiError(404, "not_found", "Conversation not found.")
        await c.execute("UPDATE handoffs SET status='resolved', resolved_at=now(), resolved_by=%s WHERE conversation_id=%s AND status='open'", (p.account_id, conversation_id))
        row = required(await (await c.execute(f"{_SELECT} WHERE cv.id=%s", (conversation_id,))).fetchone(), "conversation")  # noqa: S608
    return _summary(row)


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def patch_conversation(conversation_id: uuid.UUID, body: ConversationPatch, rt: RT, p: Tenant) -> Any:
    d = body.model_dump(exclude_unset=True)
    async with tx(rt, p) as c:
        cv = await (await c.execute("SELECT lead_stage FROM conversations WHERE id=%s", (conversation_id,))).fetchone()
        if cv is None:
            raise ApiError(404, "not_found", "Conversation not found.")
        if cv["lead_stage"] == "won":
            raise ApiError(409, "already_won", "A won deal can't be changed here.")
        if "lead_stage" in d and d["lead_stage"]:
            await c.execute("UPDATE conversations SET lead_stage=%s, lost_reason=%s WHERE id=%s", (d["lead_stage"], d.get("lost_reason") if d["lead_stage"] == "lost" else None, conversation_id))
        if "selling_stopped" in d and d["selling_stopped"] is not None:
            await c.execute("UPDATE conversations SET selling_stopped=%s WHERE id=%s", (d["selling_stopped"], conversation_id))
        row = required(await (await c.execute(f"{_SELECT} WHERE cv.id=%s", (conversation_id,))).fetchone(), "conversation")  # noqa: S608
    return _summary(row)
