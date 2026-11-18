"""Sign-in to the founder's console (ADR 0047) and its security screen (ADR 0048). Mounted only by the admin application. The first proof is an
authenticator code; the answer is either a session or, when the sign-in looks suspicious, a ticket that needs a WhatsApp code too. No request carries a
phone number: there is one founder, set at deployment, so there is nothing to type, enumerate or trigger. Every wrong answer looks the same.
Seeing sessions and attempts needs a session; ending someone else's session also needs a fresh authenticator code."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Operator, client_ip
from salesai.api.errors import ApiError  # noqa: F401
from salesai.modules.auth.admin import SignIn
from salesai.modules.auth.device import ClientInfo, clean_geo

router = APIRouter(prefix="/admin/v1/auth", tags=["admin-auth"])
DEVICE_COOKIE = "admin_device"


def client_info(rt: Any, request: Request) -> ClientInfo:
    """Where a request came from: the address (behind the trusted proxy), the browser's own description, and the proxy's country header when one is configured."""
    header = rt.settings.admin_geo_header
    return ClientInfo(ip=client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:300] or None,
                      geo=clean_geo(request.headers.get(header)) if header else None)


class SignInIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)


class TicketIn(BaseModel):
    ticket: str = Field(min_length=10, max_length=2000)


class StepUpVerify(TicketIn):
    code: str = Field(min_length=1, max_length=12)


class ExtendIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)


class RevokeIn(BaseModel):
    code: str = Field(min_length=1, max_length=12, description="a fresh authenticator code: ending a session is as protected as signing in")
    forget_browser: bool = Field(True, description="also stop trusting that browser, so it needs the WhatsApp code again")


class RevokeAllIn(BaseModel):
    code: str = Field(min_length=1, max_length=12)
    include_this_session: bool = Field(False, description="sign out everywhere, this browser too, and forget every trusted browser")


class SessionOut(BaseModel):
    id: uuid.UUID
    status: str = Field(description="active | ended | expired")
    is_current: bool
    device: str = Field(description="for example 'Chrome 142 on Windows'")
    user_agent: str | None
    browser_id: str | None = Field(description="the same value means the same browser")
    ip: str | None
    network: str | None
    country: str | None
    where: str
    usual_network: bool = Field(description="the network the founder's last good sign-in came from")
    method: str | None = Field(description="authenticator | authenticator+whatsapp | extended")
    step_up_reason: str | None
    started_at: datetime
    last_seen_at: datetime | None
    ended_at: datetime | None
    ended_reason: str | None
    expires_at: datetime
    duration_s: int


class EventOut(BaseModel):
    id: int
    action: str
    at: datetime
    detail: dict[str, Any]


def _answer(rt: Any, response: Response, result: SignIn) -> dict[str, Any]:
    if result.session is not None:
        if result.device_token:      # a trusted browser: only ever sent back to the sign-in endpoints, never readable by scripts
            response.set_cookie(DEVICE_COOKIE, result.device_token, max_age=rt.settings.admin_device_trust_days * 86400, httponly=True,
                                samesite="strict", secure=rt.settings.env != "dev", path="/admin/v1/auth")
        return {"access_token": result.session.access_token, "expires_in": result.session.expires_in, "role": result.session.role}
    assert result.ticket is not None
    return {"step_up": "whatsapp_code", "reason": result.reason, "ticket": result.ticket.access_token, "expires_in": result.ticket.expires_in}


@router.post("/sign-in", summary="The authenticator code. Returns a session, or a ticket when a WhatsApp code is needed as well")
async def sign_in(body: SignInIn, rt: RT, request: Request, response: Response) -> dict[str, Any]:
    result = await rt.admin_auth.sign_in(body.code, client_info(rt, request), request.cookies.get(DEVICE_COOKIE))
    return _answer(rt, response, result)


@router.post("/step-up/send", status_code=202, summary="Send the second-layer WhatsApp code to the founder (needs a ticket)")
async def step_up_send(body: TicketIn, rt: RT, request: Request) -> dict[str, Any]:
    await rt.admin_auth.send_step_up_code(body.ticket, client_info(rt, request))
    return {"status": "sent"}


@router.post("/step-up/verify", summary="The WhatsApp code. Returns the console session")
async def step_up_verify(body: StepUpVerify, rt: RT, request: Request, response: Response) -> dict[str, Any]:
    result = await rt.admin_auth.verify_step_up(body.ticket, body.code, client_info(rt, request), request.cookies.get(DEVICE_COOKIE))
    return _answer(rt, response, result)


@router.post("/extend", summary="Extend the session with a fresh authenticator code")
async def extend(body: ExtendIn, rt: RT, p: Operator, request: Request) -> dict[str, Any]:
    s = await rt.admin_auth.extend(p, body.code, client_info(rt, request))
    return {"access_token": s.access_token, "expires_in": s.expires_in, "role": s.role}


@router.post("/logout", status_code=204, summary="End this session now (only this one; it needs no code)")
async def logout(rt: RT, p: Operator) -> None:
    await rt.admin_auth.logout(p)


@router.get("/me", summary="Who is signed in and until when")
async def me(rt: RT, p: Operator) -> dict[str, Any]:
    return {"phone": p.phone, "role": p.role, "expires_at": await rt.admin_auth.expires_at(p)}


@router.get("/sessions", response_model=list[SessionOut], summary="Every console session: where from, on what, how it was proven, for how long")
async def sessions(rt: RT, p: Operator, limit: int = 50) -> list[dict[str, Any]]:
    return await rt.admin_auth.list_sessions(p, min(max(limit, 1), 200))


@router.get("/events", response_model=list[EventOut], summary="Console sign-in activity, newest first: sign-ins, refused attempts, locks, ended sessions")
async def events(rt: RT, p: Operator, limit: int = 100) -> list[dict[str, Any]]:  # noqa: ARG001
    return await rt.admin_auth.list_events(min(max(limit, 1), 500))


@router.post("/sessions/{session_id}/revoke", status_code=204, summary="End one session. Needs a fresh authenticator code")
async def revoke_session(session_id: uuid.UUID, body: RevokeIn, rt: RT, p: Operator, request: Request) -> None:
    await rt.admin_auth.revoke_session(p, session_id, body.code, client_info(rt, request), forget_browser=body.forget_browser)


@router.post("/sessions/revoke-others", summary="End every other session (or all, with include_this_session). Needs a fresh authenticator code")
async def revoke_others(body: RevokeAllIn, rt: RT, p: Operator, request: Request) -> dict[str, int]:
    ended = await rt.admin_auth.revoke_others(p, body.code, client_info(rt, request), everywhere=body.include_this_session)
    return {"ended": ended}
