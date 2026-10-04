from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field

from salesai.api.deps import RT, Owner, Tenant, tx
from salesai.api.errors import ApiError
from salesai.db import jsonb
from salesai.modules.catalog import repo
from salesai.modules.tenants import upsert_account
from salesai.phone import normalize_phone

router = APIRouter(tags=["business"])


# ---- validated settings (limits and shapes live in the backend, not the frontend)
class SalesSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proactiveness: Literal["low", "medium", "high"] | None = None
    may_mention_offers: bool | None = None
    honorific: str | None = Field(default=None, max_length=12)
    handoff_value_threshold: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    language_default: Literal["en", "hi", "hinglish"] | None = None


class ConversationSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_check_ms: int | None = Field(default=None, ge=100, le=10_000)
    max_wait_ms: int | None = Field(default=None, ge=1_000, le=60_000)
    min_quiet_complete_ms: int | None = Field(default=None, ge=200, le=10_000)
    min_quiet_incomplete_ms: int | None = Field(default=None, ge=500, le=20_000)
    default_gap_ms: int | None = Field(default=None, ge=500, le=30_000)
    owner_pause_minutes: int | None = Field(default=None, ge=1, le=1440)
    business_hours_behavior: Literal["reply_normally", "slower", "wait_for_open"] | None = None
    nudge_after_minutes: int | None = Field(default=None, ge=30, le=1380)
    history_messages: int | None = Field(default=None, ge=5, le=50)


class Dist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mu: float = Field(ge=0, le=12)
    sigma: float = Field(ge=0.01, le=2)
    min_ms: int = Field(ge=0, le=60_000)
    max_ms: int = Field(ge=0, le=60_000)


class TimingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_delay: Dist | None = None
    typing_ms_per_char: Dist | None = None
    part_gap: Dist | None = None
    max_total_delay_ms: int | None = Field(default=None, ge=1000, le=60_000)
    slower_multiplier: float | None = Field(default=None, ge=1, le=6)


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str | None = Field(default=None, max_length=500)
    hours: str | dict[str, Any] | None = None
    delivery: str | None = Field(default=None, max_length=1000)
    delivery_areas: list[str] | None = None
    payment_modes: list[str] | None = None
    returns: str | None = Field(default=None, max_length=1500)
    phone: str | None = Field(default=None, max_length=40)
    facts: list[str] | None = Field(default=None, max_length=60)


class BusinessPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=60)
    profile: ProfileIn | None = None
    sales_settings: SalesSettingsIn | None = None
    conversation_settings: ConversationSettingsIn | None = None
    timing_params: TimingIn | None = None


class NumberOut(BaseModel):
    id: uuid.UUID
    channel: str
    display_phone: str
    verified_name: str | None
    status: str
    quality_rating: str | None
    status_reason: str | None
    coexistence: bool


class BusinessOut(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    status: str
    ai_enabled: bool
    plan: str
    profile: dict[str, Any]
    sales_settings: dict[str, Any]
    conversation_settings: dict[str, Any]
    timing_params: dict[str, Any]
    limits: dict[str, Any]
    numbers: list[NumberOut]


class AiToggle(BaseModel):
    enabled: bool


class TeamMemberOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str
    role: Literal["owner", "staff"]
    notify: bool


class TeamIn(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    name: str = Field(min_length=1, max_length=80)
    role: Literal["owner", "staff"] = "staff"


class TeamPatch(BaseModel):
    notify: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)


async def _load(rt, p) -> BusinessOut:  # noqa: ANN001
    async with tx(rt, p) as c:
        b = await (await c.execute("SELECT * FROM businesses WHERE id=%s", (p.business_id,))).fetchone()
        ns = await (await c.execute("SELECT * FROM whatsapp_numbers ORDER BY created_at")).fetchall()
    return BusinessOut(**{k: b[k] for k in ("id", "name", "timezone", "status", "ai_enabled", "plan", "profile", "sales_settings", "conversation_settings", "timing_params", "limits")},
                       numbers=[NumberOut(**{k: n[k] for k in ("id", "channel", "display_phone", "verified_name", "status", "quality_rating", "status_reason", "coexistence")}) for n in ns])


@router.get("/business", response_model=BusinessOut)
async def get_business(rt: RT, p: Tenant) -> BusinessOut:
    return await _load(rt, p)


@router.patch("/business", response_model=BusinessOut)
async def patch_business(body: BusinessPatch, rt: RT, p: Owner) -> BusinessOut:
    async with tx(rt, p) as c:
        d = body.model_dump(exclude_unset=True, mode="json", exclude_none=False)
        if "name" in d and d["name"]:
            await c.execute("UPDATE businesses SET name=%s WHERE id=%s", (d["name"], p.business_id))
        if "timezone" in d and d["timezone"]:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(d["timezone"])
            except Exception as e:  # noqa: BLE001
                raise ApiError(422, "invalid_timezone", "Unknown timezone.") from e
            await c.execute("UPDATE businesses SET timezone=%s WHERE id=%s", (d["timezone"], p.business_id))
        for col in ("profile", "sales_settings", "conversation_settings", "timing_params"):
            if col in d and d[col] is not None:
                patch = {k: v for k, v in d[col].items() if v is not None or col == "profile"}
                if col == "timing_params":
                    for k in ("read_delay", "typing_ms_per_char", "part_gap"):
                        if k in patch and patch[k]["min_ms"] > patch[k]["max_ms"]:
                            raise ApiError(422, "invalid_timing", f"{k}: min must not exceed max.")
                await c.execute(f"UPDATE businesses SET {col} = {col} || %s::jsonb WHERE id=%s", (jsonb(patch), p.business_id))  # noqa: S608
                await repo.audit(c, p.business_id, p.account_id, "owner", f"{col}.updated", "business", str(p.business_id), {"fields": sorted(patch)})
    return await _load(rt, p)


@router.post("/business/ai", response_model=BusinessOut, summary="Pause or resume the AI for the whole business")
async def toggle_ai(body: AiToggle, rt: RT, p: Tenant) -> BusinessOut:
    async with tx(rt, p) as c:
        await c.execute("UPDATE businesses SET ai_enabled=%s WHERE id=%s", (body.enabled, p.business_id))
        await repo.audit(c, p.business_id, p.account_id, p.role, "ai.resumed" if body.enabled else "ai.paused", "business", str(p.business_id))
    return await _load(rt, p)


@router.get("/numbers", response_model=list[NumberOut])
async def numbers(rt: RT, p: Tenant) -> Any:
    return (await _load(rt, p)).numbers


# ---- team (numbers are identities: adding a person means adding their WhatsApp number)
@router.get("/team", response_model=list[TeamMemberOut])
async def team(rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        rows = await (await c.execute(
            "SELECT bu.id, bu.name, a.phone, bu.role, bu.notify FROM business_users bu JOIN accounts a ON a.id=bu.account_id ORDER BY bu.created_at")).fetchall()
    return rows


@router.post("/team", response_model=TeamMemberOut, status_code=201)
async def add_member(body: TeamIn, rt: RT, p: Owner) -> Any:
    try:
        phone = normalize_phone(body.phone)
    except ValueError as e:
        raise ApiError(422, "invalid_phone", "Enter a valid mobile number with country code.") from e
    async with rt.db.system_tx() as s:
        acct_id = await upsert_account(s, phone, body.name)
    async with tx(rt, p) as c:
        r = await (await c.execute(
            "INSERT INTO business_users (business_id, account_id, name, role) VALUES (%s,%s,%s,%s) RETURNING id, name, role, notify",
            (p.business_id, acct_id, body.name, body.role))).fetchone()
        await repo.audit(c, p.business_id, p.account_id, "owner", "team.added", "business_user", str(r["id"]), {"role": body.role})
    return {**r, "phone": phone}


@router.patch("/team/{member_id}", response_model=TeamMemberOut)
async def patch_member(member_id: uuid.UUID, body: TeamPatch, rt: RT, p: Owner) -> Any:
    d = body.model_dump(exclude_unset=True)
    async with tx(rt, p) as c:
        for k, v in d.items():
            if v is not None:
                await c.execute(f"UPDATE business_users SET {k}=%s WHERE id=%s", (v, member_id))  # noqa: S608
        r = await (await c.execute("SELECT bu.id, bu.name, a.phone, bu.role, bu.notify FROM business_users bu JOIN accounts a ON a.id=bu.account_id WHERE bu.id=%s", (member_id,))).fetchone()
    if r is None:
        raise ApiError(404, "not_found", "Team member not found.")
    return r


@router.delete("/team/{member_id}", status_code=204)
async def remove_member(member_id: uuid.UUID, rt: RT, p: Owner) -> None:
    async with tx(rt, p) as c:
        row = await (await c.execute("SELECT account_id, role FROM business_users WHERE id=%s", (member_id,))).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "Team member not found.")
        if row["account_id"] == p.account_id:
            raise ApiError(409, "cannot_remove_self", "You can't remove yourself.")
        owners = (await (await c.execute("SELECT count(*) AS n FROM business_users WHERE role='owner'")).fetchone())["n"]
        if row["role"] == "owner" and owners <= 1:
            raise ApiError(409, "last_owner", "A business needs at least one owner.")
        await c.execute("DELETE FROM business_users WHERE id=%s", (member_id,))
        await repo.audit(c, p.business_id, p.account_id, "owner", "team.removed", "business_user", str(member_id))
