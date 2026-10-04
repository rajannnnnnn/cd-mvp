"""Catalog persistence + the DB-backed pricing service (INV-1, INV-2 plumbing, negotiation state)."""
from __future__ import annotations

from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from salesai.modules.catalog import PolicyIn, ProductIn, VariantIn, repo
from salesai.modules.pricing import PricingService


def saree(price="1000", floor="800", steps=3, **kw) -> ProductIn:
    return ProductIn(name="Banarasi Silk Saree", description="Pure silk, zari border", category="sarees", variants=[VariantIn(
        name="Red", availability="limited", stock_qty=4,
        policy=PolicyIn(disclosure="fixed", list_price=D(price), negotiable=True, ai_may_negotiate=True,
                        concession_steps=steps, floor_price=D(floor), round_to=D("10"), **kw))])


async def setup_conv(world, shop, phone="+919822222222"):
    await world.customer_says(shop, phone, "hi")
    await world.drain(["inbound.events"])
    return (await world.conv(shop, phone))["id"]


async def test_policy_validation_mirrors_db_rules():
    with pytest.raises(ValidationError):
        PolicyIn(disclosure="fixed")                                           # needs list price
    with pytest.raises(ValidationError):
        PolicyIn(disclosure="range", range_min=D(10), range_max=D(5))
    with pytest.raises(ValidationError):
        PolicyIn(disclosure="fixed", list_price=D(100), ai_may_negotiate=True, negotiable=False)
    with pytest.raises(ValidationError):
        PolicyIn(disclosure="fixed", list_price=D(100), floor_price=D(120))    # floor above list
    with pytest.raises(ValidationError):
        PolicyIn(disclosure="fixed", list_price=D(100), negotiable=True, ai_may_negotiate=True, concession_steps=0)


async def test_create_and_read_product_never_exposes_floor(world):
    shop = await world.make_shop()
    async with world.rt.db.tenant(shop.business_id) as c:
        pid = await repo.create_product(c, shop.business_id, saree(price="9000", floor="8123.45"))
        p = await repo.get_product(c, pid)
        items, total = await repo.list_products(c)
    assert p.variants[0].policy.floor_set is True
    dumped = p.model_dump_json() + "".join(i.model_dump_json() for i in items)
    assert "8123" not in dumped and "floor_price" not in dumped and total == 1


async def test_ai_negotiation_requires_a_floor(world):
    shop = await world.make_shop()
    async with world.rt.db.tenant(shop.business_id) as c:
        with pytest.raises(ValueError, match="floor"):
            await repo.create_product(c, shop.business_id, ProductIn(name="X", variants=[VariantIn(policy=PolicyIn(
                disclosure="fixed", list_price=D(100), negotiable=True, ai_may_negotiate=True, concession_steps=2))]))


async def test_audit_trail_records_floor_changes_without_the_value(world):
    shop = await world.make_shop()
    async with world.rt.db.tenant(shop.business_id) as c:
        await repo.create_product(c, shop.business_id, saree(price="9000", floor="7777.77"))
    rows = await world.q(shop, "SELECT action, detail FROM audit_log WHERE action='pricing.updated'")
    assert rows and rows[0]["detail"]["floor"] == "changed"
    assert "7777" not in str(rows)


async def test_negotiation_flow_through_the_service_persists_state(world):
    shop = await world.make_shop()
    conv_id = await setup_conv(world, shop)
    async with world.rt.db.tenant(shop.business_id) as c:
        pid = await repo.create_product(c, shop.business_id, saree())
        vid = (await repo.get_product(c, pid)).variants[0].id
    svc = PricingService(world.rt.db)
    q = await svc.evaluate(shop.business_id, conv_id, vid, ask="price")
    assert q.decision.kind == "quote" and q.decision.values[0].amount == D("1000.00")
    kinds = []
    for _ in range(6):
        e = await svc.evaluate_and_save(shop.business_id, conv_id, vid, ask="counter", counter=D("500"),
                               quantity=1, advance_payment=True, repeat_customer=True)
        kinds.append(e.decision.kind)
        if e.decision.kind == "handoff":
            break
    assert kinds == ["concede", "concede", "concede", "hold", "handoff"]
    rows = await world.q(shop, "SELECT status, step, held_below_floor, current_offer FROM negotiations ORDER BY created_at")
    [neg] = rows
    assert neg["status"] == "handed_off" and neg["step"] == 3


async def test_pricing_role_is_the_only_floor_reader_and_decision_has_no_floor(world):
    shop = await world.make_shop()
    conv_id = await setup_conv(world, shop, "+919833333333")
    async with world.rt.db.tenant(shop.business_id) as c:
        pid = await repo.create_product(c, shop.business_id, saree(floor="911.11"))
        vid = (await repo.get_product(c, pid)).variants[0].id
    e = await PricingService(world.rt.db).evaluate_and_save(shop.business_id, conv_id, vid, ask="discount", advance_payment=True, repeat_customer=True)
    assert "911.11" not in str(e.decision.for_llm())
    [neg] = await world.q(shop, "SELECT history FROM negotiations")
    assert "911.11" not in str(neg)


async def test_retrieval_returns_relevant_variants_for_large_catalogs(world):
    shop = await world.make_shop()
    async with world.rt.db.tenant(shop.business_id) as c:
        for i in range(40):
            await repo.create_product(c, shop.business_id, ProductIn(name=f"Cotton Kurta {i}", category="kurtas",
                variants=[VariantIn(policy=PolicyIn(disclosure="fixed", list_price=D(500 + i)))]))
        await repo.create_product(c, shop.business_id, ProductIn(name="Wedding Lehenga", category="lehengas",
            variants=[VariantIn(policy=PolicyIn(disclosure="on_request"))]))
        hits = await repo.retrieve(c, "do you have any lehenga for wedding?", limit=5)
    assert hits and hits[0]["product_name"] == "Wedding Lehenga"
    assert all("floor" not in k for k in hits[0])
