from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Any_, client_ip
from salesai.api.errors import ApiError
from salesai.modules.auth import TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])


class OtpRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=20, examples=["+91 98765 43210"])


class OtpSent(BaseModel):
    status: Literal["sent"]
    expires_in: int


class OtpVerify(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class Tokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    business_id: uuid.UUID | None
    role: Literal["owner", "staff", "operator"]


class BusinessChoice(BaseModel):
    id: uuid.UUID
    name: str
    role: str


class ChooseBusiness(BaseModel):
    choose_business: Literal[True] = True
    businesses: list[BusinessChoice]
    ticket: str


class RefreshIn(BaseModel):
    refresh_token: str


class SelectIn(BaseModel):
    ticket: str
    business_id: uuid.UUID


class SwitchIn(BaseModel):
    business_id: uuid.UUID


class MeOut(BaseModel):
    account_id: uuid.UUID
    phone: str
    name: str | None
    role: Literal["owner", "staff", "operator"]
    language: Literal["en", "hi"]
    business: BusinessChoice | None
    businesses: list[BusinessChoice]
    impersonated: bool


class DeviceOut(BaseModel):
    id: uuid.UUID
    device: str | None
    ip: str | None
    signed_in_at: Any
    current: bool


def _tokens(pair: TokenPair) -> Tokens:
    return Tokens(access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in,
                  business_id=pair.business_id, role=pair.role)


@router.post("/otp/request", response_model=OtpSent, summary="Send a one-time login code to a WhatsApp number")
async def otp_request(body: OtpRequest, request: Request, rt: RT) -> Any:
    return await rt.auth.request_otp(body.phone, client_ip(request))


@router.post("/otp/verify", response_model=Tokens | ChooseBusiness, summary="Exchange the code for tokens")
async def otp_verify(body: OtpVerify, request: Request, rt: RT) -> Any:
    res = await rt.auth.verify_otp(body.phone, body.code, client_ip(request), (request.headers.get("user-agent") or "")[:120])
    return res if isinstance(res, dict) else _tokens(res)


@router.post("/refresh", response_model=Tokens)
async def refresh(body: RefreshIn, request: Request, rt: RT) -> Tokens:
    return _tokens(await rt.auth.refresh(body.refresh_token, client_ip(request)))


@router.post("/select-business", response_model=Tokens)
async def select_business(body: SelectIn, request: Request, rt: RT) -> Tokens:
    return _tokens(await rt.auth.select_business(body.ticket, body.business_id, client_ip(request)))


@router.post("/switch-business", response_model=Tokens)
async def switch_business(body: SwitchIn, request: Request, rt: RT, p: Any_) -> Tokens:
    return _tokens(await rt.auth.switch_business(p, body.business_id, client_ip(request)))


@router.post("/logout", status_code=204)
async def logout(rt: RT, p: Any_) -> None:
    await rt.auth.logout(p)


@router.get("/me", response_model=MeOut)
async def me(rt: RT, p: Any_) -> MeOut:
    async with rt.db.system_tx() as c:
        a = await (await c.execute("SELECT name, language FROM accounts WHERE id=%s", (p.account_id,))).fetchone()
        ms = await (await c.execute(
            "SELECT bu.business_id, bu.role, b.name FROM business_users bu JOIN businesses b ON b.id=bu.business_id WHERE bu.account_id=%s AND b.status<>'churned' ORDER BY b.name",
            (p.account_id,))).fetchall()
    choices = [BusinessChoice(id=m["business_id"], name=m["name"], role=m["role"]) for m in ms]
    cur = next((m for m in choices if m.id == p.business_id), None)
    if p.business_id and cur is None:      # operator impersonating
        async with rt.db.system_tx() as c:
            b = await (await c.execute("SELECT id, name FROM businesses WHERE id=%s", (p.business_id,))).fetchone()
        cur = BusinessChoice(id=b["id"], name=b["name"], role="owner") if b else None
    return MeOut(account_id=p.account_id, phone=p.phone, name=a["name"], role=p.role, language=a["language"], business=cur,
                 businesses=choices, impersonated=p.impersonated_by is not None)


@router.get("/sessions", response_model=list[DeviceOut])
async def sessions(rt: RT, p: Any_) -> Any:
    return await rt.auth.sessions(p)


@router.delete("/sessions/{family_id}", status_code=204)
async def revoke_session(family_id: uuid.UUID, rt: RT, p: Any_) -> None:
    if not await rt.auth.revoke_family(p, family_id):
        raise ApiError(404, "not_found", "That device was not found.")
