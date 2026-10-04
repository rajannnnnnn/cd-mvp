from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Owner, Tenant, idempotent, tx
from salesai.api.errors import ApiError
from salesai.modules.catalog import OfferIn, OfferOut, PolicyIn, ProductIn, ProductOut, ProductPatch, VariantIn, VariantOut, repo

router = APIRouter(tags=["catalog"])
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


class ProductList(BaseModel):
    items: list[ProductOut]
    total: int


@router.get("/products", response_model=ProductList)
async def list_products(rt: RT, p: Tenant, search: str | None = None, category: str | None = None, active: bool | None = None,
                        limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0) -> Any:
    async with tx(rt, p) as c:
        items, total = await repo.list_products(c, search=search, category=category, active=active, limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.post("/products", response_model=ProductOut, status_code=201, summary="Create a product with variants and pricing (owner)")
async def create_product(body: ProductIn, response: Response, rt: RT, p: Owner, idempotency_key: IdemKey = None) -> Any:
    async def run(c):  # noqa: ANN001
        pid = await repo.create_product(c, p.business_id, body, actor=p.account_id)
        out = await repo.get_product(c, pid)
        return 201, out.model_dump(mode="json")
    status, resp = await idempotent(rt, p, idempotency_key, body.model_dump(mode="json"), run)
    response.status_code = status
    return resp


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: uuid.UUID, rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        out = await repo.get_product(c, product_id)
    if out is None:
        raise ApiError(404, "not_found", "Product not found.")
    return out


@router.patch("/products/{product_id}", response_model=ProductOut)
async def patch_product(product_id: uuid.UUID, body: ProductPatch, rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        await repo.update_product(c, p.business_id, product_id, body, actor=p.account_id)
        return await repo.get_product(c, product_id)


@router.delete("/products/{product_id}", status_code=204)
async def delete_product(product_id: uuid.UUID, rt: RT, p: Owner) -> None:
    async with tx(rt, p) as c:
        await repo.delete_product(c, p.business_id, product_id, actor=p.account_id)


@router.post("/products/{product_id}/variants", response_model=ProductOut, status_code=201)
async def add_variant(product_id: uuid.UUID, body: VariantIn, rt: RT, p: Owner) -> Any:
    async with tx(rt, p) as c:
        if await repo.get_product(c, product_id) is None:
            raise ApiError(404, "not_found", "Product not found.")
        await repo.add_variant(c, p.business_id, product_id, body, actor=p.account_id)
        return await repo.get_product(c, product_id)


class VariantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    attributes: dict[str, Any] | None = None
    availability: str | None = Field(default=None, pattern="^(in_stock|limited|made_to_order|out_of_stock)$")
    stock_qty: int | None = Field(default=None, ge=0)
    is_default: bool | None = None
    active: bool | None = None


@router.patch("/variants/{variant_id}", status_code=204, summary="Edit a variant's stock, availability or name (staff may update stock)")
async def patch_variant(variant_id: uuid.UUID, body: VariantPatch, rt: RT, p: Tenant) -> None:
    async with tx(rt, p) as c:
        await repo.update_variant(c, p.business_id, variant_id, body.model_dump(exclude_unset=True), actor=p.account_id)


@router.put("/variants/{variant_id}/policy", response_model=VariantOut, summary="Set the pricing policy. The floor price is WRITE-ONLY.")
async def put_policy(variant_id: uuid.UUID, body: PolicyIn, rt: RT, p: Owner) -> Any:
    async with tx(rt, p) as c:
        await repo.set_policy(c, p.business_id, variant_id, body, actor=p.account_id)
        row = await (await c.execute("SELECT product_id FROM product_variants WHERE id=%s", (variant_id,))).fetchone()
        prod = await repo.get_product(c, row["product_id"])
    return next(v for v in prod.variants if v.id == variant_id)


# ---- offers
@router.get("/offers", response_model=list[OfferOut])
async def list_offers(rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        return await repo.list_offers(c)


@router.post("/offers", response_model=OfferOut, status_code=201)
async def create_offer(body: OfferIn, rt: RT, p: Owner) -> Any:
    async with tx(rt, p) as c:
        oid = await repo.create_offer(c, p.business_id, body, actor=p.account_id)
        return next(o for o in await repo.list_offers(c) if o.id == oid)


@router.put("/offers/{offer_id}", response_model=OfferOut)
async def update_offer(offer_id: uuid.UUID, body: OfferIn, rt: RT, p: Owner) -> Any:
    async with tx(rt, p) as c:
        await repo.update_offer(c, p.business_id, offer_id, body, actor=p.account_id)
        return next(o for o in await repo.list_offers(c) if o.id == offer_id)


@router.delete("/offers/{offer_id}", status_code=204)
async def delete_offer(offer_id: uuid.UUID, rt: RT, p: Owner) -> None:
    async with tx(rt, p) as c:
        await repo.delete_offer(c, p.business_id, offer_id, actor=p.account_id)


# ---- owner voice examples (FR-CF-7)
class StyleIn(BaseModel):
    situation: str = Field(min_length=1, max_length=60, examples=["greeting", "price_question", "complaint", "thanks", "goodbye"])
    customer_text: str | None = Field(default=None, max_length=500)
    owner_reply: str = Field(min_length=1, max_length=800)


class StyleOut(StyleIn):
    id: uuid.UUID
    source: str


@router.get("/style-examples", response_model=list[StyleOut])
async def list_style(rt: RT, p: Tenant) -> Any:
    async with tx(rt, p) as c:
        return await (await c.execute("SELECT id, situation, customer_text, owner_reply, source FROM style_examples ORDER BY created_at DESC")).fetchall()


@router.post("/style-examples", response_model=StyleOut, status_code=201)
async def add_style(body: StyleIn, rt: RT, p: Owner) -> Any:
    async with tx(rt, p) as c:
        return await (await c.execute(
            "INSERT INTO style_examples (business_id, situation, customer_text, owner_reply) VALUES (%s,%s,%s,%s) RETURNING id, situation, customer_text, owner_reply, source",
            (p.business_id, body.situation.strip().lower(), body.customer_text, body.owner_reply))).fetchone()


@router.delete("/style-examples/{example_id}", status_code=204)
async def delete_style(example_id: uuid.UUID, rt: RT, p: Owner) -> None:
    async with tx(rt, p) as c:
        r = await c.execute("DELETE FROM style_examples WHERE id=%s", (example_id,))
        if r.rowcount == 0:
            raise ApiError(404, "not_found", "Example not found.")
