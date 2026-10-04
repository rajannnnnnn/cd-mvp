"""Inbound path: webhook -> store -> route (INV-6, INV-7 inputs, INV-10 inputs, FR-CV-1, FR-CV-9)."""
from __future__ import annotations

import json
import uuid

from salesai.modules.channels.simulator import build_message_payload
from salesai.modules.channels.whatsapp import sign
from tests.world import BACKEND


async def test_message_is_stored_routed_and_versioned(world):
    shop = await world.make_shop()
    await world.customer_says(shop, "+919800000001", "Hi, do you have silk sarees?")
    await world.drain(["inbound.events"])
    conv = await world.conv(shop, "+919800000001")
    assert conv["version"] == 1 and conv["state"] == "listening" and conv["last_inbound_at"] is not None
    [m] = await world.q(shop, "SELECT * FROM messages WHERE conversation_id=%s", conv["id"])
    assert m["body"] == "Hi, do you have silk sarees?" and m["direction"] == "in" and not m["answered"]
    # the end-of-turn check was scheduled for exactly this version
    stats = (await world.rt.queue.stats(["conversation.turns"]))[0]
    assert stats.pending >= 1


async def test_duplicate_delivery_by_meta_message_id_is_ignored(world):
    shop = await world.make_shop()
    wamid = f"wamid.DUP{uuid.uuid4().hex[:10]}"
    for _ in range(3):
        await world.customer_says(shop, "+919800000002", "hello", wamid=wamid)
    await world.drain(["inbound.events"])
    msgs = await world.q(shop, "SELECT 1 FROM messages")
    assert len(msgs) == 1
    conv = await world.conv(shop, "+919800000002")
    assert conv["version"] == 1                      # redelivery must not bump the version either


async def test_reprocessing_the_same_webhook_job_has_no_side_effects(world):
    shop = await world.make_shop()
    await world.customer_says(shop, "+919800000003", "price?")
    await world.rt.relay.publish_batch()
    async with world.rt.db.system_tx() as c:
        wid = (await (await c.execute("SELECT max(id) AS i FROM webhook_events")).fetchone())["i"]
    from salesai.queue.base import Job, JobSpec
    job = Job("0", JobSpec("inbound.events", "route_webhook", {"webhook_event_id": wid}), 1)
    await world.rt.router.route_webhook(job)
    await world.rt.router.route_webhook(job)
    assert len(await world.q(shop, "SELECT 1 FROM messages")) == 1
    assert (await world.conv(shop, "+919800000003"))["version"] == 1


async def test_bad_signature_is_rejected_and_not_stored(world):
    shop = await world.make_shop()
    payload = build_message_payload(shop.phone_number_id, shop.business_phone, "+919800000004", "x")
    raw = json.dumps(payload).encode()
    async with world.rt.db.system_tx() as c:
        before = (await (await c.execute("SELECT count(*) AS n FROM webhook_events")).fetchone())["n"]
    from salesai.modules.channels.ingress import accept_webhook
    for sig in (None, "sha256=deadbeef", sign("wrong-secret", raw)):
        code, _ = await accept_webhook(world.rt.db, world.s.meta_app_secret, raw, sig)
        assert code == 401
    async with world.rt.db.system_tx() as c:
        after = (await (await c.execute("SELECT count(*) AS n FROM webhook_events")).fetchone())["n"]
    assert before == after


async def test_non_meta_payload_shape_is_rejected(world):
    from salesai.modules.channels.ingress import accept_webhook
    raw = json.dumps({"hello": "world"}).encode()
    code, _ = await accept_webhook(world.rt.db, world.s.meta_app_secret, raw, sign(world.s.meta_app_secret, raw))
    assert code == 400


async def test_new_message_cancels_pending_work_for_the_old_version(world):
    shop = await world.make_shop()
    await world.customer_says(shop, "+919800000005", "hi")
    await world.drain(["inbound.events"])
    await world.customer_says(shop, "+919800000005", "i want a saree")
    await world.drain(["inbound.events"])
    conv = await world.conv(shop, "+919800000005")
    assert conv["version"] == 2
    if BACKEND == "postgres":        # the shared queue suite proves supersession on every backend; this checks the rows
        async with world.rt.db.system_tx() as c:
            rows = await (await c.execute(
                "SELECT entity_version, status FROM jobs WHERE entity_key=%s AND queue='conversation.turns' ORDER BY entity_version",
                (f"conversation:{conv['id']}",))).fetchall()
        assert [(r["entity_version"], r["status"]) for r in rows] == [(1, "cancelled"), (2, "pending")]


async def test_owner_reply_from_business_app_pauses_ai_and_supersedes_plans(world):
    shop = await world.make_shop()
    await world.customer_says(shop, "+919800000006", "need help")
    await world.drain(["inbound.events"])
    await world.owner_replies_from_app(shop, "+919800000006", "Hello, I will help you")
    await world.drain(["inbound.events"])
    conv = await world.conv(shop, "+919800000006")
    assert conv["ai_paused_until"] is not None and conv["ai_paused_reason"] == "owner_reply"
    assert conv["version"] == 2 and conv["state"] == "idle"
    msgs = await world.q(shop, "SELECT sender, answered FROM messages WHERE conversation_id=%s ORDER BY created_at", conv["id"])
    assert [m["sender"] for m in msgs] == ["customer", "owner"] and all(m["answered"] for m in msgs)
    if BACKEND == "postgres":
        async with world.rt.db.system_tx() as c:
            pend = await (await c.execute("SELECT status FROM jobs WHERE entity_key=%s AND entity_version=1", (f"conversation:{conv['id']}",))).fetchall()
        assert all(r["status"] == "cancelled" for r in pend)


async def test_echo_for_unknown_chat_is_never_ingested(world):
    """Privacy: owner's personal chats must not flow into the system."""
    shop = await world.make_shop()
    await world.owner_replies_from_app(shop, "+919800000007", "personal message to a friend")
    await world.drain(["inbound.events"])
    assert await world.q(shop, "SELECT 1 FROM customers") == []
    assert await world.q(shop, "SELECT 1 FROM messages") == []


async def test_personal_contact_and_opt_out_are_recorded_but_never_queued_for_a_reply(world):
    shop = await world.make_shop()
    await world.customer_says(shop, "+919800000008", "hi")
    await world.drain(["inbound.events"])
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE customers SET is_personal=true WHERE wa_id='919800000008'")
    n0 = (await world.rt.queue.stats(["conversation.turns"]))[0].pending
    await world.customer_says(shop, "+919800000008", "are you there?")
    await world.drain(["inbound.events"])
    msgs = await world.q(shop, "SELECT body, answered FROM messages WHERE direction='in' ORDER BY created_at")
    assert [m["body"] for m in msgs] == ["hi", "are you there?"] and msgs[1]["answered"]
    assert (await world.rt.queue.stats(["conversation.turns"]))[0].pending == n0

    await world.customer_says(shop, "+919800000009", "STOP")
    await world.drain(["inbound.events"])
    [cust] = await world.q(shop, "SELECT opted_out FROM customers WHERE wa_id='919800000009'")
    assert cust["opted_out"] is True
    await world.customer_says(shop, "+919800000009", "start")
    await world.drain(["inbound.events"])
    [cust] = await world.q(shop, "SELECT opted_out FROM customers WHERE wa_id='919800000009'")
    assert cust["opted_out"] is False


async def test_owner_number_messages_go_to_owner_channel_not_customers(world):
    shop = await world.make_shop()
    await world.customer_says(shop, shop.owner_phone, "stop")
    await world.drain(["inbound.events"])
    assert await world.q(shop, "SELECT 1 FROM customers") == []
    [om] = await world.q(shop, "SELECT body, direction FROM owner_messages")
    assert om["body"] == "stop" and om["direction"] == "in"


async def test_status_updates_record_delivery_and_read_timestamps(world):
    shop = await world.make_shop()
    await world.customer_says(shop, "+919800000010", "hi")
    await world.drain(["inbound.events"])
    conv = await world.conv(shop, "+919800000010")
    wamid = f"wamid.OUT{uuid.uuid4().hex[:10]}"
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute(
            "INSERT INTO messages (business_id, conversation_id, wa_message_id, direction, sender, status, body) VALUES (%s,%s,%s,'out','ai','sent','hello')",
            (shop.business_id, conv["id"], wamid))
    await world.status(shop, "+919800000010", wamid, "delivered")
    await world.status(shop, "+919800000010", wamid, "read")
    await world.status(shop, "+919800000010", wamid, "delivered")      # out-of-order: must not regress
    await world.drain(["inbound.events"])
    [m] = await world.q(shop, "SELECT status, delivered_at, read_at FROM messages WHERE wa_message_id=%s", wamid)
    assert m["status"] == "read" and m["delivered_at"] and m["read_at"]


async def test_webhook_for_unknown_number_raises_operator_alert(world):
    payload = build_message_payload("sim-unknown-number", "+919000000099", "+919800000011", "hello")
    await world.post(payload)
    await world.drain(["inbound.events"])
    async with world.rt.db.system_tx() as c:
        rows = await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='unknown_number'")).fetchall()
    assert rows


async def test_two_shops_never_mix_conversations(world):
    a, b = await world.make_shop("A"), await world.make_shop("B")
    await world.customer_says(a, "+919811111111", "for A")
    await world.customer_says(b, "+919811111111", "for B")
    await world.drain(["inbound.events"])
    assert [m["body"] for m in await world.q(a, "SELECT body FROM messages")] == ["for A"]
    assert [m["body"] for m in await world.q(b, "SELECT body FROM messages")] == ["for B"]
