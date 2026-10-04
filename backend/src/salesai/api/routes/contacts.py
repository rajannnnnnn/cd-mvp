from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Tenant, decode_cursor, encode_cursor, tx
from salesai.api.errors import ApiError
from salesai.db import required
from salesai.phone import normalize_phone, wa_id

router = APIRouter(tags=["contacts"])


class CustomerOut(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str
    is_personal: bool
    opted_out: bool
    first_seen_at: datetime
    last_seen_at: datetime
    won_deals: int = 0


class CustomerList(BaseModel):
    items: list[CustomerOut]
    next_cursor: str | None


class CustomerPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    is_personal: bool | None = None
    opted_out: bool | None = None


class PersonalIn(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    name: str | None = Field(default=None, max_length=120)


def _out(r: dict[str, Any]) -> CustomerOut:
    return CustomerOut(id=r["id"], name=r["name"], phone="+" + r["wa_id"], is_personal=r["is_personal"], opted_out=r["opted_out"],
                       first_seen_at=r["first_seen_at"], last_seen_at=r["last_seen_at"], won_deals=r.get("won", 0))


@router.get("/customers", response_model=CustomerList)
async def customers(rt: RT, p: Tenant, search: str | None = None, personal: bool | None = None, opted_out: bool | None = None,
                    limit: Annotated[int, Query(ge=1, le=100)] = 30, cursor: str | None = None) -> Any:
    where: list[str] = ["TRUE"]
    args: list[Any] = []
    if search:
        where.append("(c.name ILIKE %s OR c.wa_id LIKE %s)")
        args += [f"%{search}%", f"%{search.lstrip('+')}%"]
    if personal is not None:
        where.append("c.is_personal = %s")
        args.append(personal)
    if opted_out is not None:
        where.append("c.opted_out = %s")
        args.append(opted_out)
    cur = decode_cursor(cursor)
    if cur:
        where.append("(c.last_seen_at, c.id::text) < (%s, %s)")
        args += [cur[0], cur[1]]
    async with tx(rt, p) as c:
        rows = await (await c.execute(
            f"""SELECT c.*, (SELECT count(*) FROM deals d JOIN conversations cv ON cv.id=d.conversation_id WHERE cv.customer_id=c.id AND d.status='won') AS won
                FROM customers c WHERE {' AND '.join(where)} ORDER BY c.last_seen_at DESC, c.id::text DESC LIMIT %s""", (*args, limit + 1))).fetchall()  # noqa: S608
    more = len(rows) > limit
    rows = rows[:limit]
    return CustomerList(items=[_out(r) for r in rows], next_cursor=encode_cursor(rows[-1]["last_seen_at"], rows[-1]["id"]) if more and rows else None)


@router.patch("/customers/{customer_id}", response_model=CustomerOut)
async def patch_customer(customer_id: uuid.UUID, body: CustomerPatch, rt: RT, p: Tenant) -> Any:
    d = body.model_dump(exclude_unset=True)
    async with tx(rt, p) as c:
        for k, v in d.items():
            if k == "opted_out":
                await c.execute("UPDATE customers SET opted_out=%s, opted_out_at=CASE WHEN %s THEN now() END WHERE id=%s", (v, v, customer_id))
            elif v is not None or k == "name":
                await c.execute(f"UPDATE customers SET {k}=%s WHERE id=%s", (v, customer_id))  # noqa: S608
        r = await (await c.execute("SELECT * FROM customers WHERE id=%s", (customer_id,))).fetchone()
        if r is None:
            raise ApiError(404, "not_found", "Customer not found.")
        if d.get("is_personal"):     # a personal contact is never answered, and anything pending for them is dropped
            await c.execute("UPDATE messages SET answered=true WHERE direction='in' AND NOT answered AND conversation_id IN (SELECT id FROM conversations WHERE customer_id=%s)", (customer_id,))
    return _out(r)


@router.post("/contacts/personal", response_model=CustomerOut, status_code=201, summary="Mark a number as a personal contact the AI must never reply to (FR-CF-9)")
async def add_personal(body: PersonalIn, rt: RT, p: Tenant) -> Any:
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise ApiError(422, "invalid_phone", "Enter a valid mobile number with country code.") from e
    async with tx(rt, p) as c:
        r = required(await (await c.execute(
            """INSERT INTO customers (business_id, wa_id, name, is_personal) VALUES (%s,%s,%s,true)
               ON CONFLICT (business_id, wa_id) DO UPDATE SET is_personal=true, name=COALESCE(customers.name, EXCLUDED.name) RETURNING *""",
            (p.bid, wa_id(phone), body.name))).fetchone(), "customer")
    return _out(r)
