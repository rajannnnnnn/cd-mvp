"""Sign-in to the founder's console: a WhatsApp code, then an authenticator code (ADR 0028). Mounted only by the admin
application. Every answer looks the same whether or not the number belongs to an operator."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Operator, client_ip
from salesai.api.errors import ApiError  # noqa: F401

router = APIRouter(prefix="/admin/v1/auth", tags=["admin-auth"])


class CodeRequest(BaseModel):
    phone: str = Field(min_length=5, max_length=24)


class CodeVerify(BaseModel):
    phone: str = Field(min_length=5, max_length=24)
    code: str = Field(min_length=1, max_length=12)


class TotpIn(BaseModel):
    ticket: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=1, max_length=12)


class ExtendIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)


@router.post("/code/request", summary="Send a sign-in code to an enrolled platform operator (the answer is the same for everyone)")
async def request_code(body: CodeRequest, rt: RT, request: Request) -> dict[str, Any]:
    await rt.admin_auth.request_code(body.phone, client_ip(request))
    return {"status": "sent"}


@router.post("/code/verify", summary="Step 1: the WhatsApp code. Returns a five-minute ticket, which is not a session")
async def verify_code(body: CodeVerify, rt: RT, request: Request) -> dict[str, Any]:
    t = await rt.admin_auth.verify_code(body.phone, body.code, client_ip(request))
    return {"ticket": t.access_token, "expires_in": t.expires_in}


@router.post("/totp", summary="Step 2: the authenticator code. Returns the console session")
async def verify_totp(body: TotpIn, rt: RT, request: Request) -> dict[str, Any]:
    s = await rt.admin_auth.verify_totp(body.ticket, body.code, client_ip(request))
    return {"access_token": s.access_token, "expires_in": s.expires_in, "role": s.role}


@router.post("/extend", summary="Extend the session with a fresh authenticator code")
async def extend(body: ExtendIn, rt: RT, p: Operator, request: Request) -> dict[str, Any]:
    s = await rt.admin_auth.extend(p, body.code, client_ip(request))
    return {"access_token": s.access_token, "expires_in": s.expires_in, "role": s.role}


@router.post("/logout", status_code=204, summary="End the session now")
async def logout(rt: RT, p: Operator) -> None:
    await rt.admin_auth.logout(p)


@router.get("/me", summary="Who is signed in and until when")
async def me(rt: RT, p: Operator) -> dict[str, Any]:
    return {"phone": p.phone, "role": p.role, "expires_at": await rt.admin_auth.expires_at(p)}
