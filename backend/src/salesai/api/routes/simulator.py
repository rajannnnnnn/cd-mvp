"""Development simulator: the 'WhatsApp network' as seen from a phone. Enabled only when SIMULATOR_ENABLED
(refused in production by configuration validation). Every action goes through the REAL signed-webhook ingress."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Tenant, tx
from salesai.api.errors import ApiError
from salesai.phone import normalize_phone, wa_id

router = APIRouter(prefix="/sim", tags=["simulator"])


class SimNumber(BaseModel):
    id: str
    display_phone: str
    verified_name: str | None
    status: str


class SimSend(BaseModel):
    business_phone: str
    from_phone: str
    text: str | None = Field(default=None, max_length=2000)
    kind: Literal["text", "audio", "image"] = "text"
    name: str | None = Field(default=None, max_length=80)


class SimAck(BaseModel):
    business_phone: str
    phone: str
    wamid: str
    status: Literal["delivered", "read"]


class SimEcho(BaseModel):
    business_phone: str
    to_phone: str
    text: str = Field(min_length=1, max_length=2000)


class SimAccount(BaseModel):
    business_phone: str
    field: Literal["account_update", "phone_number_quality_update"]
    event: str = Field(max_length=60)


class SimMessage(BaseModel):
    id: int
    direction: Literal["to_user", "from_user"]
    kind: str
    body: str | None
    template_name: str | None
    wa_message_id: str | None
    reaction_to: str | None
    status: str
    typing: bool
    created_at: Any


def _enabled(rt) -> None:  # noqa: ANN001
    if not rt.settings.simulator_enabled:
        raise ApiError(404, "not_found", "Not found.")


async def _own_number(rt, p, business_phone: str) -> None:  # noqa: ANN001
    async with tx(rt, p) as c:
        r = await (await c.execute("SELECT 1 FROM whatsapp_numbers WHERE display_phone=%s AND channel='simulator'", (business_phone,))).fetchone()
    if r is None:
        raise ApiError(404, "not_found", "That simulated number does not belong to your business.")


@router.get("/inbox", response_model=list[SimMessage], summary="DEV ONLY: what the platform sender (login codes) delivered to a phone")
async def inbox(rt: RT, phone: str, after_id: int = 0) -> Any:
    _enabled(rt)
    try:
        ph = normalize_phone(phone)
    except ValueError:
        return []
    return await rt.sim.thread(ph, "platform", after_id, 20)


@router.get("/numbers", response_model=list[SimNumber])
async def numbers(rt: RT, p: Tenant) -> Any:
    _enabled(rt)
    async with tx(rt, p) as c:
        rows = await (await c.execute("SELECT id, display_phone, verified_name, status FROM whatsapp_numbers WHERE channel='simulator' ORDER BY created_at")).fetchall()
    return [{**r, "id": str(r["id"])} for r in rows]


@router.post("/send", status_code=202, summary="A person sends a WhatsApp message to the business (customer or owner phone)")
async def send(body: SimSend, rt: RT, p: Tenant) -> dict[str, str]:
    _enabled(rt)
    await _own_number(rt, p, body.business_phone)
    try:
        frm = normalize_phone(body.from_phone)
    except ValueError as e:
        raise ApiError(422, "invalid_phone", "Enter a valid mobile number with country code.") from e
    wamid = await rt.sim.user_sends(body.business_phone, frm, body.text, kind=body.kind, name=body.name)
    return {"wamid": wamid}


@router.get("/thread", response_model=list[SimMessage])
async def thread(rt: RT, p: Tenant, phone: str, business_phone: str, after_id: Annotated[int, Query(ge=0)] = 0) -> Any:
    _enabled(rt)
    await _own_number(rt, p, business_phone)
    try:
        ph = normalize_phone(phone)
    except ValueError:
        return []
    return await rt.sim.thread(ph, business_phone, after_id)


@router.post("/ack", status_code=202)
async def ack(body: SimAck, rt: RT, p: Tenant) -> None:
    _enabled(rt)
    await _own_number(rt, p, body.business_phone)
    await rt.sim.acknowledge(body.business_phone, normalize_phone(body.phone), body.wamid, body.status)


@router.post("/echo", status_code=202, summary="The owner replies from the WhatsApp Business app (coexistence echo)")
async def echo(body: SimEcho, rt: RT, p: Tenant) -> None:
    _enabled(rt)
    await _own_number(rt, p, body.business_phone)
    await rt.sim.owner_replies_from_app(body.business_phone, normalize_phone(body.to_phone), body.text)


@router.post("/account-event", status_code=202, summary="Simulate a Meta account event (disconnection, quality change)")
async def account_event(body: SimAccount, rt: RT, p: Tenant) -> None:
    _enabled(rt)
    await _own_number(rt, p, body.business_phone)
    await rt.sim.account_event(body.business_phone, body.field, body.event)
