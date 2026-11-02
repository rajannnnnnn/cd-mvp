"""The gateway reads Meta; the agent reads the conversation (ADR 0025, rule B9).

Before this, one class both parsed Meta's wire format and wrote conversations, messages and handoffs, so the agent
imported the gateway's parser. Now the gateway turns each webhook into channel-neutral events and hands them to the agent
through a mailbox (`channel_events`); the agent never sees the wire format. Only the event id travels in the queue, since
a message can carry anything a customer typed. Delivery is at-least-once and every step is safe to repeat."""
from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from salesai.modules.channels import parse_webhook
from salesai.modules.channels.base import (
    AccountEvent,
    EchoMessage,
    InboundMessage,
    StatusUpdate,
    event_from_dict,
    event_to_dict,
)
from salesai.queue.base import Job, JobSpec
from tests.test_conversation_flow import shop_with_catalog

NOW = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)

SAMPLES = [
    InboundMessage("wamid.A", "919800000001", "pn-1", "text", "saree dikhao", NOW, sender_name="Priya", reply_to="wamid.Z", raw={"id": "wamid.A", "text": {"body": "x"}}),
    InboundMessage("wamid.B", "919800000001", "pn-1", "reaction", None, NOW, reaction_to="wamid.A"),
    EchoMessage("wamid.C", "pn-1", "919800000001", "text", "I'll call you", NOW, raw={"id": "wamid.C"}),
    StatusUpdate("wamid.D", "pn-1", "failed", NOW, recipient_id="919800000001", error_code="131047", error="Re-engagement message"),
    AccountEvent("pn-1", "waba-9", "disconnected", {"event": "PARTNER_REMOVED", "nested": {"a": [1, 2]}}),
    AccountEvent(None, "waba-9", "quality", {"quality_rating": "yellow"}),
]


@pytest.mark.parametrize("ev", SAMPLES, ids=lambda e: type(e).__name__ + ":" + str(getattr(e, "kind", getattr(e, "status", ""))))
def test_every_event_type_survives_the_hand_off_unchanged(ev):
    wire = json.loads(json.dumps(event_to_dict(ev)))                                   # exactly what a database row would hold
    assert event_from_dict(wire) == ev


def test_an_event_of_an_unknown_kind_is_refused_not_guessed():
    with pytest.raises(ValueError):
        event_from_dict({"type": "something_new", "data": {}})


async def _events(world, shop):
    return await world.q(shop, "SELECT * FROM channel_events ORDER BY created_at, id")


async def test_the_gateway_hands_over_events_and_the_agent_then_acts_on_them(world):
    shop, _ = await shop_with_catalog(world)
    phone = f"+9198{uuid.uuid4().int % 10**8:08d}"
    await world.customer_says(shop, phone, "do you have red sarees?")
    await world.drain(["inbound.events"])                                              # the gateway's part only
    [ev] = await _events(world, shop)
    assert (ev["kind"], ev["status"]) == ("inbound", "pending")
    assert await world.conv(shop, phone) is None                                        # the agent has not acted yet
    async with world.rt.db.system_tx() as c:
        w = await (await c.execute("SELECT processed_at FROM webhook_events ORDER BY id DESC LIMIT 1")).fetchone()
    assert w["processed_at"] is not None                                                # the gateway's own work is complete
    await world.drain(["conversation.events"])                                         # the agent's part
    assert (await world.conv(shop, phone)) is not None
    assert [e["status"] for e in await _events(world, shop)] == ["done"]


async def test_only_the_event_id_travels_in_the_queue(world):
    shop, _ = await shop_with_catalog(world)
    secret = f"my card is totally not 4111 {uuid.uuid4().hex[:8]}"
    await world.customer_says(shop, f"+9198{uuid.uuid4().int % 10**8:08d}", secret)
    await world.drain(["inbound.events"])
    async with world.rt.db.system_tx() as c:
        outbox = await (await c.execute("SELECT payload::text AS p FROM outbox WHERE event_type='channel.event_received'")).fetchall()
        jobs = await (await c.execute("SELECT payload::text AS p FROM jobs WHERE kind='channel_event'")).fetchall()
    assert outbox and jobs and not any(secret in r["p"] for r in [*outbox, *jobs])


async def test_events_for_one_customer_share_an_ordering_key_and_different_customers_do_not(world):
    shop, _ = await shop_with_catalog(world)
    a, b = (f"+9198{uuid.uuid4().int % 10**8:08d}" for _ in range(2))
    await world.customer_says(shop, a, "hi")
    await world.customer_says(shop, a, "any colours?")
    await world.customer_says(shop, b, "hello")
    await world.drain(["inbound.events"])
    async with world.rt.db.system_tx() as c:
        keys = [r["ordering_key"] for r in await (await c.execute(
            "SELECT ordering_key FROM outbox WHERE event_type='channel.event_received' AND business_id=%s ORDER BY seq", (shop.business_id,))).fetchall()]
    from collections import Counter
    assert sorted(Counter(keys).values()) == [1, 2]                                    # a's two events share a key; b's has its own


async def test_an_unknown_number_alerts_the_platform_team_and_hands_nothing_over(world):
    ghost = f"pn-ghost-{uuid.uuid4().hex[:8]}"
    from salesai.modules.channels.simulator import build_message_payload
    status, _ = await world.post(build_message_payload(ghost, "+910000000000", "+919800000099", "hi"))
    assert status == 200
    await world.drain(["inbound.events"])
    async with world.rt.db.system_tx() as c:
        alerts = await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='unknown_number' AND detail->>'phone_number_id'=%s", (ghost,))).fetchall()
        events = await (await c.execute("SELECT 1 FROM outbox WHERE event_type='channel.event_received' AND payload::text LIKE %s", (f"%{ghost}%",))).fetchall()
    assert alerts and events == []                                                      # INV-12: never silent


async def test_running_the_gateway_job_twice_hands_over_once(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, f"+9198{uuid.uuid4().int % 10**8:08d}", "price?")
    await world.rt.relay.publish_batch()
    async with world.rt.db.system_tx() as c:
        wid = (await (await c.execute("SELECT max(id) AS i FROM webhook_events")).fetchone())["i"]
    job = Job("0", JobSpec("inbound.events", "route_webhook", {"webhook_event_id": wid}), 1)
    await world.rt.router.route_webhook(job)
    await world.rt.router.route_webhook(job)
    assert len(await _events(world, shop)) == 1


async def test_running_the_agent_job_twice_acts_once(world):
    shop, _ = await shop_with_catalog(world)
    phone = f"+9198{uuid.uuid4().int % 10**8:08d}"
    await world.customer_says(shop, phone, "price?")
    await world.drain(["inbound.events", "conversation.events"])
    [ev] = await _events(world, shop)
    job = Job("0", JobSpec("conversation.events", "channel_event", {"channel_event_id": str(ev["id"])}, business_id=shop.business_id), 1)
    await world.rt.intake.handle(job)                                                   # at-least-once redelivery
    assert len(await world.q(shop, "SELECT 1 FROM messages WHERE direction='in'")) == 1


async def test_a_failure_in_the_agent_is_recorded_and_the_event_is_retried(world, monkeypatch):
    shop, _ = await shop_with_catalog(world)
    phone = f"+9198{uuid.uuid4().int % 10**8:08d}"
    real, calls = world.rt.intake._inbound, []

    async def flaky(number, ev):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database hiccup")
        return await real(number, ev)
    monkeypatch.setattr(world.rt.intake, "_inbound", flaky)
    await world.customer_says(shop, phone, "hello")
    await world.drain_for(3.0, ["inbound.events", "conversation.events"])
    [ev] = await _events(world, shop)
    assert (ev["status"], len(calls)) == ("done", 2) and (await world.conv(shop, phone)) is not None
    assert len(await world.q(shop, "SELECT 1 FROM messages WHERE direction='in'")) == 1  # NFR-3: not lost, not doubled


async def test_retention_removes_handled_events_after_a_week_and_never_pending_ones(world):
    from salesai.scheduler import Scheduler
    shop, _ = await shop_with_catalog(world)
    old = datetime.now(UTC) - timedelta(days=10)
    async with world.rt.db.tenant(shop.business_id) as c:
        for status in ("done", "pending"):
            await c.execute("INSERT INTO channel_events (business_id, number_id, kind, event, status, created_at) VALUES (%s,%s,'status','{}',%s,%s)",
                            (shop.business_id, shop.number_id, status, old))
    await Scheduler(world.rt).tick_retention()
    assert [e["status"] for e in await _events(world, shop)] == ["pending"]             # an unhandled message is never discarded (NFR-3)


async def test_the_parser_is_the_gateways_and_returns_the_same_events_as_before(world):
    from salesai.modules.channels.simulator import build_message_payload
    evs = parse_webhook(build_message_payload("pn-x", "+910000000001", "+919800000010", "hello"))
    assert [type(e).__name__ for e in evs] == ["InboundMessage"] and evs[0].text == "hello"
    assert dataclasses.is_dataclass(evs[0])
