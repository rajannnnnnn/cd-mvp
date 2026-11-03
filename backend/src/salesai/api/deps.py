"""FastAPI dependencies: authentication, roles, tenant transactions, pagination, idempotency.
The tenant is ALWAYS derived from the authenticated session — never from anything the client sends."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from pydantic import BaseModel

from salesai.api.errors import ApiError
from salesai.db import Conn, jsonb
from salesai.modules.auth import AuthError, Principal
from salesai.runtime import Runtime


def get_rt(request: Request) -> Runtime:
    return request.app.state.rt


RT = Annotated[Runtime, Depends(get_rt)]


async def principal(request: Request, rt: RT, authorization: Annotated[str | None, Header()] = None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("unauthenticated", "Sign in to continue.")
    return await rt.auth.authenticate(authorization.split(None, 1)[1])


async def tenant_principal(p: Annotated[Principal, Depends(principal)]) -> Principal:
    if p.business_id is None or p.role not in ("owner", "staff"):
        raise ApiError(403, "forbidden", "This action needs a business account.")
    return p


async def owner_principal(p: Annotated[Principal, Depends(tenant_principal)]) -> Principal:
    if p.role != "owner":
        raise ApiError(403, "forbidden", "Only the business owner can do this.")
    return p


@lru_cache(maxsize=16)
def _networks(spec: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(x.strip(), strict=False) for x in spec.split(",") if x.strip())


async def admin_gate(request: Request, rt: RT) -> None:
    """The optional address allowlist of the founder's console (ADR 0028), checked on every admin request, sign-in included."""
    allowed = _networks(rt.settings.admin_allowed_ips)
    if not allowed:
        return
    try:
        addr = ipaddress.ip_address(client_ip(request) or "")
    except ValueError:
        addr = None
    if addr is None or not any(addr in n for n in allowed):
        raise ApiError(403, "ip_not_allowed", "The admin console cannot be opened from this address.")


async def operator_principal(rt: RT, authorization: Annotated[str | None, Header()] = None) -> Principal:
    """The platform operator, signed in to the admin console with a second factor. A shop-app token never gets here: it is signed
    with a different secret, so it does not even verify."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("unauthenticated", "Sign in to continue.")
    return await rt.admin_auth.authenticate(authorization.split(None, 1)[1])


async def setup_principal(p: Annotated[Principal, Depends(principal)]) -> Principal:
    if p.role != "setup":
        raise ApiError(403, "forbidden", "This is only for accounts that have not created a business yet.")
    return p


Any_ = Annotated[Principal, Depends(principal)]
Setup = Annotated[Principal, Depends(setup_principal)]          # signed in, no business yet (onboarding)
Tenant = Annotated[Principal, Depends(tenant_principal)]       # owner or staff
Owner = Annotated[Principal, Depends(owner_principal)]         # owner only (floors, team, settings)
Operator = Annotated[Principal, Depends(operator_principal)]


@asynccontextmanager
async def tx(rt: Runtime, p: Principal) -> AsyncIterator[Conn]:
    async with rt.db.tenant(p.bid) as c:
        yield c


# ---------------------------------------------------------------- pagination (opaque cursors)
def encode_cursor(ts: datetime, id_: uuid.UUID | int) -> str:
    return base64.urlsafe_b64encode(json.dumps([ts.isoformat(), str(id_)]).encode()).decode()


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        ts, id_ = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(ts), id_
    except Exception as e:  # noqa: BLE001
        raise ApiError(422, "invalid_cursor", "That page cursor is not valid.") from e


class Page(BaseModel):
    next_cursor: str | None = None


# ---------------------------------------------------------------- idempotency for retried writes
async def idempotent(rt: Runtime, p: Principal, key: str | None, payload: Any,
                     run: Callable[[Conn], Awaitable[tuple[int, dict[str, Any]]]]) -> tuple[int, dict[str, Any]]:
    """Runs `run` in a tenant transaction. With an Idempotency-Key header, a retry returns the first response."""
    assert p.business_id is not None
    if not key:
        async with tx(rt, p) as c:
            return await run(c)
    h = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    async with tx(rt, p) as c:
        row = await (await c.execute("SELECT request_hash, status, response FROM idempotency_keys WHERE key=%s", (key,))).fetchone()
        if row:
            if row["request_hash"] != h:
                raise ApiError(422, "idempotency_key_reuse", "That Idempotency-Key was used with a different request.")
            return row["status"], row["response"]
        status, resp = await run(c)
        await c.execute("INSERT INTO idempotency_keys (business_id, key, request_hash, status, response) VALUES (%s,%s,%s,%s,%s)",
                        (p.business_id, key, h, status, jsonb(json.loads(json.dumps(resp, default=str)))))
        return status, resp


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd and request.app.state.rt.settings.trust_proxy:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
