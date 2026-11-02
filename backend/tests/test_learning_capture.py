"""Learning loop, recording step (ADR 0031): when the owner answers a conversation the AI handed over, the answer is
recorded WITH its context — the customer's question, the handoff reason, the stage — so a later step can work out the
narrowest scope it applies to (an owner's "no refund" may mean one order, one product, or the whole shop). Nothing is
learned and no behaviour changes here.

"Never learn across shops" (proposed INV-13; Meta's AI Providers clause forbids using WhatsApp data to train or
improve models except exclusively for one business): records are row-level-secured per shop, and only an allow-list
of modules may touch them — none of which talks to a model vendor."""
from __future__ import annotations

import pathlib

from salesai.modules.handoffs import learning
from salesai.obs import LEARNING_CAPTURE_FAILED
from tests.test_api import V1, hdr, login
from tests.test_conversation_flow import shop_with_catalog

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "salesai"
PHONE = "+919600000201"


async def _x(world, shop, sql: str, *args) -> None:
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute(sql, args or None)


async def _handed_over(world, shop, question: str = "do you give refunds?") -> dict:
    """A customer asked something, and the AI handed the conversation to the owner."""
    await world.customer_says(shop, PHONE, question)
    await world.drain(["inbound.events", "conversation.events"])
    conv = await world.conv(shop, PHONE)
    await _x(world, shop, "INSERT INTO handoffs (business_id, conversation_id, reason, note) VALUES (%s,%s,'unknown_answer','refund question')",
             shop.business_id, conv["id"])
    await _x(world, shop, "UPDATE messages SET answered=false WHERE conversation_id=%s AND direction='in'", conv["id"])
    return conv


async def _observations(world, shop) -> list[dict]:
    return await world.q(shop, "SELECT * FROM learning_observations ORDER BY created_at")


async def test_an_owner_answer_from_the_business_app_is_recorded_with_its_context(world):
    shop, _ = await shop_with_catalog(world)
    conv = await _handed_over(world, shop)
    await world.owner_replies_from_app(shop, PHONE, "No refund on sarees, sorry.")
    await world.drain(["inbound.events", "conversation.events"])
    [o] = await _observations(world, shop)
    assert o["owner_text"] == "No refund on sarees, sorry." and "refunds" in o["customer_text"]
    assert (o["source"], o["reason"], o["conversation_id"], o["customer_id"]) == ("business_app", "unknown_answer", conv["id"], conv["customer_id"])
    assert o["handoff_id"] is not None and "lead_stage" in o["context"]


async def test_an_owner_answer_from_the_dashboard_is_recorded_too(world, client):
    shop, _ = await shop_with_catalog(world)
    conv = await _handed_over(world, shop)
    owner = await login(client, shop.owner_phone)
    r = await client.post(f"{V1}/conversations/{conv['id']}/messages", headers=hdr(owner), json={"text": "Exchange only, within 7 days."})
    assert r.status_code == 201
    [o] = await _observations(world, shop)
    assert (o["source"], o["owner_text"]) == ("dashboard", "Exchange only, within 7 days.")


async def test_nothing_is_recorded_when_the_ai_had_not_handed_over(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, PHONE, "hi")
    await world.drain(["inbound.events", "conversation.events"])
    await world.owner_replies_from_app(shop, PHONE, "Hello! How can I help?")
    await world.drain(["inbound.events", "conversation.events"])
    assert await _observations(world, shop) == []


async def test_a_failure_while_recording_never_loses_the_owners_reply(world, monkeypatch):
    shop, _ = await shop_with_catalog(world)
    conv = await _handed_over(world, shop)

    async def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(learning, "_record", boom)
    before = LEARNING_CAPTURE_FAILED._value.get()
    await world.owner_replies_from_app(shop, PHONE, "No refund on sarees, sorry.")
    await world.drain(["inbound.events", "conversation.events"])
    rows = await world.q(shop, "SELECT body FROM messages WHERE conversation_id=%s AND sender='owner'", conv["id"])
    assert [r["body"] for r in rows] == ["No refund on sarees, sorry."]                 # the reply itself survived
    [c] = await world.q(shop, "SELECT ai_paused_reason FROM conversations WHERE id=%s", conv["id"])
    assert c["ai_paused_reason"] == "owner_reply"                                       # and so did the AI pause
    assert LEARNING_CAPTURE_FAILED._value.get() == before + 1                           # counted, not silent (INV-12)


async def test_one_shop_never_sees_another_shops_records(world):
    a, _ = await shop_with_catalog(world)
    b, _ = await shop_with_catalog(world)
    for shop in (a, b):
        await _handed_over(world, shop)
        await world.owner_replies_from_app(shop, PHONE, f"answer from {shop.business_id}")
        await world.drain(["inbound.events", "conversation.events"])
    for shop in (a, b):
        rows = await _observations(world, shop)
        assert len(rows) == 1 and {r["business_id"] for r in rows} == {shop.business_id}   # both sides have rows: not vacuous


def test_only_the_allowed_modules_touch_learning_records():
    """Proposed INV-13: per-shop only. Model adapters and anything that could pool shops never read these records."""
    # retention deletes old records shop by shop through the shop's own role; it never reads them across shops
    allowed = {"modules/handoffs/learning.py", "modules/tenants/lifecycle.py", "scheduler.py"}
    users = {str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if "learning_observations" in p.read_text()}
    assert users == allowed, users
