"""Owner loop: alerts, commands, configuration by chat with read-back, daily summary, disconnections."""
from __future__ import annotations

import re

from tests.test_conversation_flow import shop_with_catalog
from tests.world import settle, sim_texts


async def owner_thread(world, shop):
    return await world.sim_thread(shop.owner_phone, shop.business_phone)


async def owner_says(world, shop, text):
    await world.net.user_sends(shop.business_phone, shop.owner_phone, text)
    await world.drain_for(0.8)


async def last_owner_reply(world, shop):
    rows = [r for r in await owner_thread(world, shop) if r["direction"] == "to_user" and r["kind"] in ("text", "template")]
    return rows[-1] if rows else None


async def code_in(world, shop, key):
    for r in reversed(await owner_thread(world, shop)):
        if r["direction"] == "to_user" and r["body"]:
            m = re.search(rf"{key} ([0-9a-f]{{6}})", r["body"])
            if m:
                return m.group(1)


async def test_handoff_alerts_the_owner_with_a_template_outside_their_window(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919600000001", "I want to talk to the owner")
    await world.drain_for(2.0)
    alert = await last_owner_reply(world, shop)
    assert alert and alert["kind"] == "template" and alert["template_name"] == "handoff_alert"       # INV-8 applies to owners too
    assert "asked to talk to you" in alert["body"]
    [om] = await world.q(shop, "SELECT purpose, kind, status FROM owner_messages WHERE direction='out'")
    assert om["purpose"] == "handoff_alert" and om["status"] == "sent"


async def test_alert_is_free_form_inside_the_owner_window_and_done_resolves_the_handoff(world):
    shop, _ = await shop_with_catalog(world)
    await owner_says(world, shop, "help")                                  # opens the owner's 24h window
    await world.customer_says(shop, "+919600000002", "the saree is damaged, I need a refund")
    await world.drain_for(2.0)
    alert = await last_owner_reply(world, shop)
    assert alert["kind"] == "text" and "complaint" in alert["body"]
    code = await code_in(world, shop, "done")
    await owner_says(world, shop, f"done {code}")
    [h] = await world.q(shop, "SELECT status FROM handoffs")
    assert h["status"] == "resolved"
    conv = await world.conv(shop, "+919600000002")
    assert conv["ai_paused_until"] is None


async def test_stop_and_start_from_the_owners_number(world):
    shop, _ = await shop_with_catalog(world)
    await owner_says(world, shop, "stop")
    [b] = await world.q(shop, "SELECT ai_enabled FROM businesses")
    assert b["ai_enabled"] is False
    await world.customer_says(shop, "+919600000003", "hello")
    await world.drain_for(1.2)
    assert await sim_texts(world, shop, "+919600000003") == []                  # FR-CV-10
    await owner_says(world, shop, "start")
    [b] = await world.q(shop, "SELECT ai_enabled FROM businesses")
    assert b["ai_enabled"] is True
    await world.customer_says(shop, "+919600000003", "hello again")
    assert await settle(world, shop, "+919600000003")


async def test_deal_alert_and_owner_marks_it_won(world):
    shop, _ = await shop_with_catalog(world)
    await owner_says(world, shop, "help")
    phone = "+919600000004"
    await world.customer_says(shop, phone, "I want to buy the silk saree")
    await settle(world, shop, phone, want=1)
    await world.customer_says(shop, phone, "Flat 4, Rose Apartments, near City Mall, Pune 411001")
    await settle(world, shop, phone, want=2)
    await world.customer_says(shop, phone, "yes confirm")
    await settle(world, shop, phone, want=3)
    await world.drain_for(1.0)
    code = await code_in(world, shop, "won")
    assert code, "owner should have been alerted with a deal code"
    await owner_says(world, shop, f"won {code}")
    [d] = await world.q(shop, "SELECT status FROM deals")
    assert d["status"] == "won"
    conv = await world.conv(shop, phone)
    assert conv["lead_stage"] == "won"


async def test_configuration_by_chat_requires_read_back_confirmation(world):
    shop, _ = await shop_with_catalog(world)
    await owner_says(world, shop, "add product Green Cotton Saree 1500")
    reply = await last_owner_reply(world, shop)
    assert "Green Cotton Saree" in reply["body"] and "yes" in reply["body"].lower()
    assert await world.q(shop, "SELECT 1 FROM products WHERE name='Green Cotton Saree'") == []     # nothing applied yet
    await owner_says(world, shop, "yes")
    [p] = await world.q(shop, "SELECT p.name, pp.list_price FROM products p JOIN product_variants v ON v.product_id=p.id JOIN pricing_policies pp ON pp.variant_id=v.id WHERE p.name='Green Cotton Saree'")
    assert p["list_price"] == 1500
    assert await world.q(shop, "SELECT 1 FROM audit_log WHERE action='product.created'")
    await owner_says(world, shop, "hours: Mon-Sat 9am-9pm")
    await owner_says(world, shop, "no")
    [b] = await world.q(shop, "SELECT profile FROM businesses")
    assert b["profile"]["hours"] == "Mon-Sat 10am-8pm"                                              # cancelled: unchanged
    await owner_says(world, shop, "hours: Mon-Sat 9am-9pm")
    await owner_says(world, shop, "yes")
    [b] = await world.q(shop, "SELECT profile FROM businesses")
    assert b["profile"]["hours"] == "Mon-Sat 9am-9pm"


async def test_floor_prices_cannot_be_set_by_chat(world):
    shop, vid = await shop_with_catalog(world)
    await owner_says(world, shop, "set minimum price of Banarasi Silk Saree to 100")
    reply = await last_owner_reply(world, shop)
    assert "dashboard" in reply["body"].lower()
    assert await world.q(shop, "SELECT 1 FROM config_proposals") == []


async def test_price_change_by_chat_updates_the_list_price(world):
    shop, vid = await shop_with_catalog(world)
    await owner_says(world, shop, "price of banarasi silk saree is 1200")
    await owner_says(world, shop, "yes")
    [pp] = await world.q(shop, "SELECT list_price FROM pricing_policies WHERE variant_id=%s", vid)
    assert pp["list_price"] == 1200


async def test_unknown_question_can_be_taught_after_confirmation(world):
    shop, _ = await shop_with_catalog(world)
    await owner_says(world, shop, "help")
    await world.customer_says(shop, "+919600000005", "do you offer gift wrapping?")
    await world.drain_for(2.0)
    code = await code_in(world, shop, "answer")
    assert code
    await owner_says(world, shop, f"answer {code} Yes, gift wrapping is free on orders above 1000")
    [b] = await world.q(shop, "SELECT profile FROM businesses")
    assert not any("gift wrapping" in f for f in b["profile"].get("facts", []))                   # not a fact until confirmed
    await owner_says(world, shop, "yes")
    [b] = await world.q(shop, "SELECT profile FROM businesses")
    assert any("gift wrapping" in f for f in b["profile"]["facts"])
    await world.customer_says(shop, "+919600000006", "do you offer gift wrapping?")
    texts = await settle(world, shop, "+919600000006")
    assert any("gift wrapping" in t.lower() for t in texts)                                       # learned: answered, not handed off


async def test_disconnection_pauses_the_ai_and_alerts_owner_and_operator(world):
    shop, _ = await shop_with_catalog(world)
    await world.net.account_event(shop.business_phone, "account_update", "DISABLED_UPDATE")
    await world.drain_for(1.5)
    [n] = await world.q(shop, "SELECT status FROM whatsapp_numbers")
    assert n["status"] == "disconnected"
    # the business number is down, so the owner is alerted from the PLATFORM sender
    rows = [r for r in await world.sim_thread(shop.owner_phone, "platform") if r["direction"] == "to_user"]
    assert rows and rows[-1]["template_name"] == "disconnect_alert" and "disconnected" in rows[-1]["body"]
    async with world.rt.db.system_tx() as c:
        rows = await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='number_disconnected' AND business_id=%s", (shop.business_id,))).fetchall()
    assert rows
    await world.customer_says(shop, "+919600000007", "hi")
    await world.drain_for(1.2)
    assert await sim_texts(world, shop, "+919600000007") == []                                     # FR-ON-4: AI stopped
    await world.net.account_event(shop.business_phone, "account_update", "ACCOUNT_RECONNECTED")
    await world.drain_for(0.8)
    [n] = await world.q(shop, "SELECT status FROM whatsapp_numbers")
    assert n["status"] == "connected"


async def test_quality_drop_raises_an_operator_alert(world):
    shop, _ = await shop_with_catalog(world)
    await world.net.account_event(shop.business_phone, "phone_number_quality_update", "FLAGGED")
    await world.drain_for(1.0)
    [n] = await world.q(shop, "SELECT quality_rating FROM whatsapp_numbers")
    assert n["quality_rating"] == "yellow"
