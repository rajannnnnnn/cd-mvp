"""Self-serve onboarding: from a verified mobile number to a live shop. The steps reuse the normal APIs (business settings, products,
WhatsApp connection); this module creates the business, reports progress, and flips the shop live."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from salesai.api.deps import RT, Any_, Owner, Setup, client_ip, tx
from salesai.api.errors import ApiError
from salesai.api.routes.auth import Tokens, _tokens
from salesai.db import jsonb, required
from salesai.modules.tenants import create_business, slugify, unique_slug, valid_slug

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

CATEGORIES = ["Clothing & fabrics", "Mobiles & electronics", "Grocery & kirana", "Jewellery", "Furniture & home", "Beauty & salon",
              "Restaurant & cafe", "Education & coaching", "Services", "Other"]


class BusinessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=80, description="The shop or business name customers know")
    owner_name: str = Field(min_length=1, max_length=80)
    category: str | None = Field(default=None, max_length=60)
    city: str | None = Field(default=None, max_length=80)
    slug: str | None = Field(default=None, max_length=40, description="Preferred web address; adjusted if taken")
    language: Literal["en", "hi", "hinglish"] = "hinglish"


class Step(BaseModel):
    key: Literal["business", "details", "products", "whatsapp", "assistant", "live"]
    title: str
    hint: str
    done: bool
    skipped: bool
    required: bool


class BusinessBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str


class OnboardingOut(BaseModel):
    has_business: bool
    business: BusinessBrief | None
    steps: list[Step]
    ready_to_go_live: bool
    complete: bool


class SlugCheck(BaseModel):
    slug: str
    available: bool
    reason: str | None = None
    suggestion: str


@router.get("/categories", response_model=list[str])
async def categories() -> Any:
    return CATEGORIES


@router.get("/slug", response_model=SlugCheck, summary="Is this web address free? (works before a business exists)")
async def slug_check(rt: RT, p: Any_, slug: Annotated[str, Query(min_length=1, max_length=60)]) -> Any:
    want = slugify(slug) if valid_slug(slug) is not None else slug
    reason = valid_slug(want)
    async with rt.db.system_tx() as c:
        taken = await (await c.execute("SELECT id FROM businesses WHERE slug=%s", (want,))).fetchone()
        mine = bool(taken and p.business_id and str(taken["id"]) == str(p.business_id))
        suggestion = await unique_slug(c, want)
    if taken and not mine:
        reason = "That web address is already used."
    return SlugCheck(slug=want, available=reason is None, reason=reason, suggestion=suggestion)


@router.post("/business", response_model=Tokens, status_code=201, summary="Create the business for a newly signed-up number")
async def create(body: BusinessCreate, request: Request, rt: RT, p: Setup) -> Tokens:
    hint = body.slug
    if hint and valid_slug(hint):
        hint = None
    async with rt.db.system_tx() as c:
        src = await (await c.execute("SELECT signup_source FROM accounts WHERE id=%s", (p.account_id,))).fetchone()
    profile = {k: v for k, v in {"category": body.category, "city": body.city}.items() if v}
    cb = await create_business(rt.db, rt.settings.master_key_bytes, name=body.name.strip(), owner_phone=p.phone, owner_name=body.owner_name.strip(),
                               plan="trial", language="en", profile=profile, ai_enabled=False, signup_source=src["signup_source"] if src else None,
                               actor="owner", slug_hint=hint)
    async with rt.db.tenant(cb.business_id) as c:
        await c.execute("UPDATE businesses SET sales_settings = sales_settings || %s::jsonb WHERE id=%s",
                        (jsonb({"language_default": body.language}), cb.business_id))
    pair = await rt.auth.switch_business(p, cb.business_id, client_ip(request))
    await rt.auth.logout(p)                                                         # the setup session is no longer needed
    return _tokens(pair)


async def _state(rt: Any, p: Any) -> OnboardingOut:
    if p.business_id is None:
        steps = [Step(key="business", title="Your business", hint="Name, type and city", done=False, skipped=False, required=True)]
        return OnboardingOut(has_business=False, business=None, steps=steps, ready_to_go_live=False, complete=False)
    async with tx(rt, p) as c:
        b = required(await (await c.execute("SELECT id, name, slug, status, profile, onboarding FROM businesses WHERE id=%s", (p.bid,))).fetchone(), "business")
        products = (await (await c.execute("SELECT count(*) AS n FROM products WHERE active")).fetchone())["n"]
        connected = (await (await c.execute("SELECT count(*) AS n FROM whatsapp_numbers WHERE status='connected'")).fetchone())["n"]
    ob = b["onboarding"] or {}
    skipped = set(ob.get("skipped", []))
    prof = b["profile"] or {}
    live = b["status"] == "active" and bool(ob.get("completed_at"))
    steps = [
        Step(key="business", title="Your business", hint="Name, type and city", done=True, skipped=False, required=True),
        Step(key="details", title="Shop details", hint="Address, opening hours, delivery, payment modes and returns: facts the assistant may state",
             done=bool(prof.get("address") or prof.get("hours") or prof.get("delivery")), skipped="details" in skipped, required=False),
        Step(key="products", title="Products or services", hint="What you sell and how it may be priced. Your lowest price stays private",
             done=products > 0, skipped=False, required=True),
        Step(key="whatsapp", title="Connect WhatsApp", hint="Connect your business number so the assistant can answer customers",
             done=connected > 0, skipped="whatsapp" in skipped, required=False),
        Step(key="assistant", title="Assistant preferences", hint="Language, how proactive it is and whether it may bargain",
             done=bool(ob.get("assistant_reviewed")), skipped="assistant" in skipped, required=False),
        Step(key="live", title="Go live", hint="Switch the assistant on", done=live, skipped=False, required=True),
    ]
    ready = products > 0
    return OnboardingOut(has_business=True, business=BusinessBrief(id=b["id"], name=b["name"], slug=b["slug"], status=b["status"]),
                         steps=steps, ready_to_go_live=ready, complete=live)


@router.get("", response_model=OnboardingOut, summary="Where this account is in onboarding")
async def state(rt: RT, p: Any_) -> Any:
    if p.role not in ("setup", "owner", "staff"):
        raise ApiError(403, "forbidden", "Onboarding is for shop owners.")
    return await _state(rt, p)


class StepAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: Literal["details", "whatsapp", "assistant"]
    action: Literal["skip", "done", "undo"]


@router.post("/steps", response_model=OnboardingOut, summary="Mark an optional step skipped or reviewed")
async def step(body: StepAction, rt: RT, p: Owner) -> Any:
    async with tx(rt, p) as c:
        row = required(await (await c.execute("SELECT onboarding FROM businesses WHERE id=%s", (p.bid,))).fetchone(), "business")
        ob = dict(row["onboarding"] or {})
        skipped = set(ob.get("skipped", []))
        if body.action == "skip":
            skipped.add(body.step)
        else:
            skipped.discard(body.step)
        if body.step == "assistant":
            ob["assistant_reviewed"] = body.action == "done"
        ob["skipped"] = sorted(skipped)
        await c.execute("UPDATE businesses SET onboarding=%s WHERE id=%s", (jsonb(ob), p.bid))
    return await _state(rt, p)


@router.post("/go-live", response_model=OnboardingOut, summary="Finish onboarding and switch the assistant on")
async def go_live(rt: RT, p: Owner) -> Any:
    st = await _state(rt, p)
    if not st.ready_to_go_live:
        raise ApiError(409, "not_ready", "Add at least one product or service before going live.")
    async with tx(rt, p) as c:
        connected = (await (await c.execute("SELECT count(*) AS n FROM whatsapp_numbers WHERE status='connected'")).fetchone())["n"]
        row = required(await (await c.execute("SELECT onboarding FROM businesses WHERE id=%s", (p.bid,))).fetchone(), "business")
        ob = dict(row["onboarding"] or {})
        ob.setdefault("completed_at", datetime.now(UTC).isoformat())
        await c.execute("UPDATE businesses SET status='active', ai_enabled=%s, onboarding=%s WHERE id=%s", (connected > 0, jsonb(ob), p.bid))
        await c.execute("INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity, entity_id) VALUES (%s,%s,'owner','onboarding.completed','business',%s)",
                        (p.bid, p.account_id, str(p.bid)))
    return await _state(rt, p)
