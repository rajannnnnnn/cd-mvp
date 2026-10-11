"""Support tickets. A shop opens one from Settings and it exists the moment it is sent (no email round trip); support answers in the
same thread. The shop sees its own tickets only (row-level security); support staff (platform operators) see all of them.
Nothing here touches prices: a ticket is text the owner chose to write."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from salesai.api.deps import RT, Operator, Tenant, tx
from salesai.api.errors import ApiError
from salesai.db import Conn, jsonb
from salesai.modules.catalog import repo

router = APIRouter(tags=["support"])

Category = Literal["question", "problem", "billing", "whatsapp", "other"]
Status = Literal["open", "answered", "resolved"]
MAX_PER_DAY = 20          # per shop: a runaway script or a bored teenager cannot fill the support queue


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=160)
    category: Category = "question"
    message: str = Field(min_length=1, max_length=4000)


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=4000)


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status


class TicketMessageOut(BaseModel):
    id: int
    author: Literal["shop", "support"]
    body: str
    created_at: datetime


class TicketOut(BaseModel):
    id: uuid.UUID
    number: int
    subject: str
    category: Category
    status: Status
    created_at: datetime
    updated_at: datetime
    last_message: str | None = None
    last_author: Literal["shop", "support"] | None = None


class TicketDetail(TicketOut):
    messages: list[TicketMessageOut]


class OperatorTicket(TicketOut):
    business_id: uuid.UUID
    business_name: str


class OperatorTicketDetail(OperatorTicket):
    messages: list[TicketMessageOut]


_LIST = """SELECT t.id, t.number, t.subject, t.category, t.status, t.created_at, t.updated_at,
                  lm.body AS last_message, lm.author AS last_author
           FROM support_tickets t
           LEFT JOIN LATERAL (SELECT body, author FROM support_messages m WHERE m.ticket_id = t.id ORDER BY m.id DESC LIMIT 1) lm ON true"""
_KEYS = ("id", "number", "subject", "category", "status", "created_at", "updated_at", "last_message", "last_author")


async def _messages(c: Conn, ticket_id: uuid.UUID) -> list[TicketMessageOut]:
    rows = await (await c.execute("SELECT id, author, body, created_at FROM support_messages WHERE ticket_id=%s ORDER BY id", (ticket_id,))).fetchall()
    return [TicketMessageOut(**r) for r in rows]


async def _detail(c: Conn, ticket_id: uuid.UUID) -> TicketDetail:
    r = await (await c.execute(f"{_LIST} WHERE t.id=%s", (ticket_id,))).fetchone()  # noqa: S608
    if r is None:
        raise ApiError(404, "not_found", "That ticket doesn't exist.")
    return TicketDetail(**{k: r[k] for k in _KEYS}, messages=await _messages(c, ticket_id))


# ---------------------------------------------------------------- the shop
@router.post("/support/tickets", response_model=TicketDetail, status_code=201, summary="Open a support ticket (it exists immediately)")
async def open_ticket(body: TicketIn, rt: RT, p: Tenant) -> TicketDetail:
    async with tx(rt, p) as c:
        n = (await (await c.execute("SELECT count(*) AS n FROM support_tickets WHERE created_at > now() - interval '1 day'")).fetchone())["n"]
        if n >= MAX_PER_DAY:
            raise ApiError(429, "too_many_tickets", "You have opened a lot of tickets today. Please add to an existing one.")
        row = await (await c.execute(
            "INSERT INTO support_tickets (business_id, opened_by, subject, category) VALUES (%s,%s,%s,%s) RETURNING id, number",
            (p.bid, p.account_id, body.subject.strip(), body.category))).fetchone()
        await c.execute("INSERT INTO support_messages (business_id, ticket_id, author, author_account, body) VALUES (%s,%s,'shop',%s,%s)",
                        (p.bid, row["id"], p.account_id, body.message.strip()))
        await repo.audit(c, p.bid, p.account_id, p.role, "support.opened", "support_ticket", str(row["id"]), {"number": row["number"], "category": body.category})
        out = await _detail(c, row["id"])
        name = (await (await c.execute("SELECT name FROM businesses WHERE id=%s", (p.bid,))).fetchone())["name"]
    async with rt.db.system_tx() as s:      # support gets told right away
        await s.execute("INSERT INTO operator_alerts (business_id, severity, kind, message, detail) VALUES (%s,'info','support_ticket',%s,%s::jsonb)",
                        (p.bid, f"Support ticket #{row['number']} from {name}: {body.subject.strip()[:80]}", jsonb({"ticket_id": str(row["id"]), "number": row["number"]})))
    return out


@router.get("/support/tickets", response_model=list[TicketOut], summary="Your shop's tickets, newest activity first")
async def my_tickets(rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        rows = await (await c.execute(f"{_LIST} ORDER BY t.updated_at DESC LIMIT 100")).fetchall()  # noqa: S608
    return [TicketOut(**{k: r[k] for k in _KEYS}) for r in rows]


@router.get("/support/tickets/{ticket_id}", response_model=TicketDetail)
async def my_ticket(ticket_id: uuid.UUID, rt: RT, p: Tenant) -> TicketDetail:
    async with tx(rt, p) as c:
        return await _detail(c, ticket_id)


@router.post("/support/tickets/{ticket_id}/messages", response_model=TicketDetail, status_code=201, summary="Add to a ticket (reopens it if it was resolved)")
async def add_message(ticket_id: uuid.UUID, body: ReplyIn, rt: RT, p: Tenant) -> TicketDetail:
    async with tx(rt, p) as c:
        r = await c.execute("UPDATE support_tickets SET status='open', updated_at=now() WHERE id=%s", (ticket_id,))
        if r.rowcount == 0:
            raise ApiError(404, "not_found", "That ticket doesn't exist.")
        await c.execute("INSERT INTO support_messages (business_id, ticket_id, author, author_account, body) VALUES (%s,%s,'shop',%s,%s)",
                        (p.bid, ticket_id, p.account_id, body.body.strip()))
        return await _detail(c, ticket_id)


@router.post("/support/tickets/{ticket_id}/resolve", response_model=TicketDetail, summary="Mark your own ticket as sorted")
async def resolve(ticket_id: uuid.UUID, rt: RT, p: Tenant) -> TicketDetail:
    async with tx(rt, p) as c:
        r = await c.execute("UPDATE support_tickets SET status='resolved', updated_at=now() WHERE id=%s", (ticket_id,))
        if r.rowcount == 0:
            raise ApiError(404, "not_found", "That ticket doesn't exist.")
        return await _detail(c, ticket_id)


# ---------------------------------------------------------------- support staff (platform operators)
_OP = _LIST.replace("FROM support_tickets t", "FROM support_tickets t JOIN businesses b ON b.id = t.business_id").replace("SELECT t.id,", "SELECT b.name AS business_name, t.business_id, t.id,")


async def _op_detail(c: Conn, ticket_id: uuid.UUID) -> OperatorTicketDetail:
    r = await (await c.execute(f"{_OP} WHERE t.id=%s", (ticket_id,))).fetchone()  # noqa: S608
    if r is None:
        raise ApiError(404, "not_found", "That ticket doesn't exist.")
    msgs = await (await c.execute("SELECT id, author, body, created_at FROM support_messages WHERE ticket_id=%s ORDER BY id", (ticket_id,))).fetchall()
    return OperatorTicketDetail(**{k: r[k] for k in (*_KEYS, "business_id", "business_name")}, messages=[TicketMessageOut(**m) for m in msgs])


@router.get("/operator/support", response_model=list[OperatorTicket], summary="All tickets, waiting-for-support first")
async def all_tickets(rt: RT, p: Operator, status: Annotated[Status | None, Query()] = None) -> Any:
    async with rt.db.system_tx() as c:
        rows = await (await c.execute(  # noqa: S608
            f"{_OP} {'WHERE t.status=%s' if status else ''} ORDER BY (t.status='open') DESC, t.updated_at DESC LIMIT 200", (status,) if status else None)).fetchall()
    return [OperatorTicket(**{k: r[k] for k in (*_KEYS, "business_id", "business_name")}) for r in rows]


@router.get("/operator/support/{ticket_id}", response_model=OperatorTicketDetail)
async def ticket_for_support(ticket_id: uuid.UUID, rt: RT, p: Operator) -> OperatorTicketDetail:
    async with rt.db.system_tx() as c:
        return await _op_detail(c, ticket_id)


@router.post("/operator/support/{ticket_id}/messages", response_model=OperatorTicketDetail, status_code=201, summary="Answer a ticket")
async def answer(ticket_id: uuid.UUID, body: ReplyIn, rt: RT, p: Operator) -> OperatorTicketDetail:
    async with rt.db.system_tx() as c:
        t = await (await c.execute("UPDATE support_tickets SET status='answered', updated_at=now() WHERE id=%s RETURNING business_id", (ticket_id,))).fetchone()
        if t is None:
            raise ApiError(404, "not_found", "That ticket doesn't exist.")
        await c.execute("INSERT INTO support_messages (business_id, ticket_id, author, author_account, body) VALUES (%s,%s,'support',%s,%s)",
                        (t["business_id"], ticket_id, p.account_id, body.body.strip()))
        return await _op_detail(c, ticket_id)


@router.put("/operator/support/{ticket_id}/status", response_model=OperatorTicketDetail)
async def set_status(ticket_id: uuid.UUID, body: StatusIn, rt: RT, p: Operator) -> OperatorTicketDetail:
    async with rt.db.system_tx() as c:
        r = await c.execute("UPDATE support_tickets SET status=%s, updated_at=now() WHERE id=%s", (body.status, ticket_id))
        if r.rowcount == 0:
            raise ApiError(404, "not_found", "That ticket doesn't exist.")
        return await _op_detail(c, ticket_id)
