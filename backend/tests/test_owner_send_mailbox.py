"""Owner alerts: the agent decides, the gateway sends (ADR 0025, rule B9).

Before this, the owner loop (the agent) chose the WhatsApp number, decrypted its token and sent the alert itself. Now it
records the alert as a queued owner message and the gateway sends it. What the owner receives is unchanged. What is new:
the agent holds no means of sending; the 24-hour window is enforced again at send time, where the hard rules live (INV-8);
a temporary failure retries only that one message instead of replaying the agent's whole notification; and only the
message id travels in the event, since an alert can carry customer names and prices."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from salesai.modules.channels.base import SendResult
from tests.test_conversation_flow import shop_with_catalog

LONG_AGO = datetime.now(UTC) - timedelta(days=3)


async def _x(world, shop, sql: str, *args) -> None:
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute(sql, args or None)


async def _user(world, shop, *, in_window: bool = True) -> dict:
    await _x(world, shop, "UPDATE business_users SET last_inbound_at=%s WHERE id=%s",
             datetime.now(UTC) if in_window else LONG_AGO, shop.business_user_id)
    [u] = await world.q(shop, "SELECT bu.id, bu.name, bu.last_inbound_at, a.phone, a.language FROM business_users bu "
                              "JOIN accounts a ON a.id=bu.account_id WHERE bu.id=%s", shop.business_user_id)
    return u


async def _messages(world, shop):
    return await world.q(shop, "SELECT * FROM owner_messages WHERE purpose<>'chat' ORDER BY created_at, id")


async def _delivered(world, shop, *, platform: bool = False) -> list[dict]:
    rows = await world.sim_thread(shop.owner_phone, "platform" if platform else shop.business_phone)
    return [r for r in rows if r["direction"] == "to_user"]


class _Channel:
    def __init__(self, results: list[SendResult]):
        self.results, self.calls = list(results), 0

    async def _go(self, *a, **k) -> SendResult:
        self.calls += 1
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    send_text = send_template = _go


def _patch_channels(world, monkeypatch, *results: SendResult) -> _Channel:
    ch = _Channel(list(results))
    ref, _ = world.rt.channels.platform_sender()

    async def number(bid, nid):
        return ref, ch
    monkeypatch.setattr(world.rt.channels, "number", number)
    monkeypatch.setattr(world.rt.channels, "platform_sender", lambda: (ref, ch))
    return ch


async def test_the_agent_requests_and_the_gateway_sends(world):
    shop, _ = await shop_with_catalog(world)
    u = await _user(world, shop)
    await world.rt.owner.send_to_owner(shop.business_id, u, "handoff_alert", "A customer needs you")
    [m] = await _messages(world, shop)
    assert m["status"] == "queued" and await _delivered(world, shop) == []          # requested, not yet sent
    await world.drain(["outbound.actions"])
    [m] = await _messages(world, shop)
    assert (m["status"], m["whatsapp_number_id"]) == ("sent", shop.number_id) and m["wa_message_id"] and m["error"] is None
    assert [r["body"] for r in await _delivered(world, shop)] == ["A customer needs you"]


async def test_the_agent_holds_no_means_of_sending(world):
    assert not hasattr(world.rt.owner, "channels")                                   # the structural proof is the boundary check (B9)


async def test_only_the_message_id_travels_in_the_event_and_job(world):
    shop, _ = await shop_with_catalog(world)
    secret = f"Ramesh wants 50 sarees {uuid.uuid4().hex[:8]}"
    await world.rt.owner.send_to_owner(shop.business_id, await _user(world, shop), "handoff_alert", secret)
    async with world.rt.db.system_tx() as c:
        outbox = await (await c.execute("SELECT payload::text AS p FROM outbox WHERE event_type='owner.message_requested'")).fetchall()
    assert outbox and not any(secret in r["p"] for r in outbox)


async def test_outside_the_window_without_a_template_nothing_is_requested(world):
    shop, _ = await shop_with_catalog(world)
    await world.rt.owner.send_to_owner(shop.business_id, await _user(world, shop, in_window=False), "chat", "hello")
    assert await _messages(world, shop) == [] and await world.q(shop, "SELECT 1 FROM owner_messages WHERE purpose='chat'") == []


async def test_outside_the_window_an_approved_template_is_used(world):
    shop, _ = await shop_with_catalog(world)
    u = await _user(world, shop, in_window=False)
    await world.rt.owner.send_to_owner(shop.business_id, u, "handoff_alert", "A customer needs you", "handoff_alert", ["Shop", "text"])
    await world.drain(["outbound.actions"])
    [m] = await _messages(world, shop)
    assert (m["kind"], m["template_name"], m["status"]) == ("template", "handoff_alert", "sent")
    [r] = await _delivered(world, shop)
    assert r["template_name"] == "handoff_alert"


async def test_the_window_is_enforced_again_at_send_time(world):
    # INV-8: requested while the window was open, but it closed before the gateway sent
    shop, _ = await shop_with_catalog(world)
    u = await _user(world, shop, in_window=True)
    await world.rt.owner.send_to_owner(shop.business_id, u, "handoff_alert", "A customer needs you")
    await _x(world, shop, "UPDATE business_users SET last_inbound_at=%s WHERE id=%s", LONG_AGO, shop.business_user_id)
    await world.drain(["outbound.actions"])
    [m] = await _messages(world, shop)
    assert m["status"] == "failed" and "window" in m["error"] and await _delivered(world, shop) == []


async def test_a_permanent_failure_marks_the_message_failed_and_alerts_the_platform_team(world, monkeypatch):
    shop, _ = await shop_with_catalog(world)
    ch = _patch_channels(world, monkeypatch, SendResult(False, error="recipient blocked", permanent="undeliverable"))
    await world.rt.owner.send_to_owner(shop.business_id, await _user(world, shop), "handoff_alert", "A customer needs you")
    await world.drain(["outbound.actions"])
    [m] = await _messages(world, shop)
    assert (m["status"], ch.calls) == ("failed", 1) and "blocked" in m["error"]
    async with world.rt.db.system_tx() as c:
        alerts = await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='owner_alert_failed' AND business_id=%s", (shop.business_id,))).fetchall()
    assert alerts                                                                       # INV-12: never silent


async def test_a_temporary_failure_retries_only_that_message_and_sends_it_once(world, monkeypatch):
    shop, _ = await shop_with_catalog(world)
    ch = _patch_channels(world, monkeypatch, SendResult(False, error="busy", retryable=True), SendResult(True, message_id="wamid.OK1"))
    await world.rt.owner.send_to_owner(shop.business_id, await _user(world, shop), "handoff_alert", "A customer needs you")
    await world.drain_for(3.0, ["outbound.actions"])
    [m] = await _messages(world, shop)
    assert (m["status"], m["wa_message_id"], ch.calls) == ("sent", "wamid.OK1", 2)


async def test_running_the_same_request_twice_sends_once(world):
    from salesai.events.outbox import emit
    shop, _ = await shop_with_catalog(world)
    await world.rt.owner.send_to_owner(shop.business_id, await _user(world, shop), "handoff_alert", "A customer needs you")
    await world.drain(["outbound.actions"])
    [m] = await _messages(world, shop)
    async with world.rt.db.tenant(shop.business_id) as c:
        await emit(c, "owner.message_requested", {"owner_message_id": m["id"]}, business_id=shop.business_id)     # at-least-once delivery
    await world.drain(["outbound.actions"])
    assert len(await _delivered(world, shop)) == 1


async def test_messages_to_one_owner_arrive_in_order(world):
    shop, _ = await shop_with_catalog(world)
    u = await _user(world, shop)
    for i in range(4):
        await world.rt.owner.send_to_owner(shop.business_id, u, "handoff_alert", f"alert {i}")
    await world.drain(["outbound.actions"])
    assert [r["body"] for r in await _delivered(world, shop)] == [f"alert {i}" for i in range(4)]


async def test_with_no_connected_number_the_platform_sender_is_used(world):
    shop, _ = await shop_with_catalog(world)
    await _x(world, shop, "UPDATE whatsapp_numbers SET status='disconnected'")
    await world.rt.owner.send_to_owner(shop.business_id, await _user(world, shop), "disconnect_alert", "Your number was disconnected")
    await world.drain(["outbound.actions"])
    [m] = await _messages(world, shop)
    assert m["status"] == "sent" and m["whatsapp_number_id"] is None                   # sent from the platform's own sender
    assert [r["body"] for r in await _delivered(world, shop, platform=True)] == ["Your number was disconnected"]


@pytest.mark.parametrize("purpose", ["handoff_alert", "deal_alert", "disconnect_alert"])
async def test_every_alert_kind_reaches_the_owner_end_to_end(world, purpose):
    shop, _ = await shop_with_catalog(world)
    await world.rt.owner.send_to_owner(shop.business_id, await _user(world, shop), purpose, f"{purpose} text")
    await world.drain(["outbound.actions"])
    assert [r["body"] for r in await _delivered(world, shop)] == [f"{purpose} text"]
