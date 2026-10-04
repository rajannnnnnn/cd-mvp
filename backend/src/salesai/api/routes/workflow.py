from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Tenant, tx
from salesai.api.errors import ApiError
from salesai.modules.handoffs import answer_gap, resolve_handoff
from salesai.modules.sales import close_deal

router = APIRouter(tags=["workflow"])


class Who(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str


class DealOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    kind: Literal["order", "visit"]
    status: Literal["pending", "won", "lost"]
    value: str | None
    items: list[dict[str, Any]]
    details: dict[str, Any]
    created_at: datetime
    closed_at: datetime | None
    customer: Who


class CloseIn(BaseModel):
    status: Literal["won", "lost"]
    lost_reason: str | None = Field(default=None, max_length=200)


class HandoffOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    reason: str
    note: str | None
    status: Literal["open", "resolved"]
    created_at: datetime
    resolved_at: datetime | None
    customer: Who
    last_message: str | None


class GapOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    question: str
    answer: str | None
    status: Literal["open", "answered", "dismissed"]
    created_at: datetime


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=1000)
    confirm: bool = Field(description="Must be true: the owner confirms the answer becomes a business fact (FR-HO-2).")


def _who(r: dict[str, Any]) -> Who:
    return Who(id=r["cid"], name=r["cname"], phone="+" + r["wa_id"])


@router.get("/deals", response_model=list[DealOut])
async def deals(rt: RT, p: Tenant, status: Literal["pending", "won", "lost"] | None = None, kind: Literal["order", "visit"] | None = None,
                limit: Annotated[int, Query(ge=1, le=200)] = 100) -> Any:
    async with tx(rt, p) as c:
        rows = await (await c.execute(
            """SELECT d.*, cu.id AS cid, cu.name AS cname, cu.wa_id FROM deals d JOIN conversations cv ON cv.id=d.conversation_id JOIN customers cu ON cu.id=cv.customer_id
               WHERE (%s::text IS NULL OR d.status=%s) AND (%s::text IS NULL OR d.kind=%s) ORDER BY d.created_at DESC LIMIT %s""", (status, status, kind, kind, limit))).fetchall()
    return [DealOut(id=r["id"], conversation_id=r["conversation_id"], kind=r["kind"], status=r["status"], value=str(r["value"]) if r["value"] is not None else None,
                    items=r["items"], details=r["details"], created_at=r["created_at"], closed_at=r["closed_at"], customer=_who(r)) for r in rows]


@router.post("/deals/{deal_id}/close", status_code=204, summary="The owner marks a captured commitment won or lost")
async def close(deal_id: uuid.UUID, body: CloseIn, rt: RT, p: Tenant) -> None:
    async with tx(rt, p) as c:
        if not await close_deal(c, p.bid, deal_id, body.status, p.account_id, body.lost_reason):
            raise ApiError(404, "not_found", "No pending deal with that id.")


@router.get("/handoffs", response_model=list[HandoffOut])
async def handoffs(rt: RT, p: Tenant, status: Literal["open", "resolved"] | None = "open", limit: Annotated[int, Query(ge=1, le=200)] = 100) -> Any:
    async with tx(rt, p) as c:
        rows = await (await c.execute(
            """SELECT h.*, cu.id AS cid, cu.name AS cname, cu.wa_id,
                      (SELECT body FROM messages m WHERE m.conversation_id=h.conversation_id AND m.direction='in' ORDER BY m.created_at DESC LIMIT 1) AS last_message
               FROM handoffs h JOIN conversations cv ON cv.id=h.conversation_id JOIN customers cu ON cu.id=cv.customer_id
               WHERE (%s::text IS NULL OR h.status=%s) ORDER BY h.created_at DESC LIMIT %s""", (status, status, limit))).fetchall()
    return [HandoffOut(id=r["id"], conversation_id=r["conversation_id"], reason=r["reason"], note=r["note"], status=r["status"], created_at=r["created_at"],
                       resolved_at=r["resolved_at"], customer=_who(r), last_message=r["last_message"]) for r in rows]


@router.post("/handoffs/{handoff_id}/resolve", status_code=204)
async def resolve(handoff_id: uuid.UUID, rt: RT, p: Tenant) -> None:
    async with tx(rt, p) as c:
        if not await resolve_handoff(c, p.bid, handoff_id, p.account_id):
            raise ApiError(404, "not_found", "No open handoff with that id.")


@router.get("/knowledge-gaps", response_model=list[GapOut])
async def gaps(rt: RT, p: Tenant, status: Literal["open", "answered", "dismissed"] | None = "open") -> Any:
    async with tx(rt, p) as c:
        return await (await c.execute("SELECT * FROM knowledge_gaps WHERE (%s::text IS NULL OR status=%s) ORDER BY created_at DESC LIMIT 200", (status, status))).fetchall()


@router.post("/knowledge-gaps/{gap_id}/answer", status_code=204)
async def answer(gap_id: uuid.UUID, body: AnswerIn, rt: RT, p: Tenant) -> None:
    if not body.confirm:
        raise ApiError(422, "confirmation_required", "Confirm that this answer should become a business fact.")
    async with tx(rt, p) as c:
        if not await answer_gap(c, p.bid, gap_id, body.answer, p.account_id, confirmed=True):
            raise ApiError(404, "not_found", "No open question with that id.")


@router.post("/knowledge-gaps/{gap_id}/dismiss", status_code=204)
async def dismiss(gap_id: uuid.UUID, rt: RT, p: Tenant) -> None:
    async with tx(rt, p) as c:
        r = await c.execute("UPDATE knowledge_gaps SET status='dismissed' WHERE id=%s AND status='open'", (gap_id,))
        if r.rowcount == 0:
            raise ApiError(404, "not_found", "No open question with that id.")
