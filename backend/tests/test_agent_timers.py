"""ADR 0038: the scheduler is the agent's own clock. Each conversation has at most one pending timer of each kind, set, moved or cancelled
as things happen (the AI replied, the customer wrote, the owner stepped in), fired by the agent's scheduler into the normal turn path, and
kept in the database so a restart loses nothing."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from salesai.modules.conversations.recovery import Reconciler
from salesai.scheduler import DUTIES, Scheduler
from tests.test_api import V1, hdr, owner
from tests.test_conversation_flow import shop_with_catalog
from tests.world import settle, sim_texts

ENTRY = ["inbound.events", "conversation.events"]


async def timers(world, shop, *, kind=None, status=None):
    async with world.rt.db.tenant(shop.business_id) as c:
        rows = await (await c.execute("SELECT * FROM agent_timers ORDER BY created_at, id")).fetchall()
    return [r for r in rows if (kind is None or r["kind"] == kind) and (status is None or r["status"] == status)]


async def pending(world, shop, kind):
    return [r for r in await timers(world, shop, kind=kind, status="pending")]


async def make_due(world, shop, kind="follow_back"):
    """Time passes: the customer went quiet hours ago. The clock decides when a timer is due from the chat's own state, so move that."""
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE conversations SET last_outbound_at = now() - interval '5 hours', last_inbound_at = now() - interval '5 hours 10 minutes'")
        conv = await (await c.execute("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1")).fetchone()
    await world.rt.timers.plan(shop.business_id, conv["id"])


async def after_ai_reply(world, shop, phone, text="silk saree price?"):
    await world.customer_says(shop, phone, text)
    texts = await settle(world, shop, phone)
    await world.drain()                                                   # the "sent" notice reaches the agent's clock
    return texts


def agent_clock(world) -> Scheduler:
    return Scheduler(world.rt, "agent")


# ---------------------------------------------------------------- follow back

async def test_an_ai_reply_to_an_interested_customer_arms_a_follow_back_with_its_reason(world):
    shop, _ = await shop_with_catalog(world)
    await after_ai_reply(world, shop, "+919800040001")
    [t] = await pending(world, shop, "follow_back")
    assert t["reason"] == "ai_replied" and t["why"] and t["detail"]["after_inbound_at"]
    assert timedelta(minutes=30) <= t["due_at"] - datetime.now(UTC) <= timedelta(hours=4, minutes=5)
    assert not await pending(world, shop, "takeover_check")


async def test_when_the_customer_writes_the_follow_back_is_cancelled_and_comes_back_after_the_next_reply(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800040002"
    await after_ai_reply(world, shop, phone)
    await world.customer_says(shop, phone, "ok let me check with my sister")
    await world.drain(ENTRY)                                             # their message is in; the AI has not answered yet
    [old] = await timers(world, shop, kind="follow_back", status="cancelled")
    assert old["cancel_reason"] == "customer_replied" and not await pending(world, shop, "follow_back")
    await settle(world, shop, phone, want=2)
    await world.drain()
    assert len(await pending(world, shop, "follow_back")) <= 1            # a new one for the new reply, never two at once


async def test_a_follow_back_fires_once_through_the_normal_turn_path_and_is_audited(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800040003"
    texts = await after_ai_reply(world, shop, phone)
    await make_due(world, shop)
    assert await agent_clock(world).tick_timers() == 1
    out = await settle(world, shop, phone, want=len(texts) + 1)
    assert len(out) == len(texts) + 1 and not re.search(r"\d{3,}|₹", out[-1])        # a gentle check-in, never a price
    [t] = await timers(world, shop, kind="follow_back", status="fired")
    assert t["fired_at"] is not None
    async with world.rt.db.tenant(shop.business_id) as c:
        runs = await (await c.execute("SELECT trigger, outcome FROM agent_runs WHERE trigger='follow_back'")).fetchall()
    assert [r["outcome"] for r in runs] == ["replied"]
    await world.drain()
    assert await agent_clock(world).tick_timers() == 0
    assert not await pending(world, shop, "follow_back")                  # at most one follow-back per customer message


# ---------------------------------------------------------------- the owner steps in

async def test_an_owner_reply_cancels_the_follow_back_and_checks_again_when_the_pause_ends(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800040004"
    await after_ai_reply(world, shop, phone)
    await world.owner_replies_from_app(shop, phone, "Hi, this is the owner, I will call you in a bit")
    await world.drain(ENTRY)
    assert not await pending(world, shop, "follow_back")
    [t] = await pending(world, shop, "takeover_check")
    assert t["reason"] == "owner_reply" and timedelta(minutes=118) <= t["due_at"] - datetime.now(UTC) <= timedelta(minutes=122)
    await asyncio_sleep_a_beat()
    await world.owner_replies_from_app(shop, phone, "Calling now")
    await world.drain(ENTRY)
    [moved] = await pending(world, shop, "takeover_check")               # still one, and later: the owner is active
    assert moved["id"] == t["id"] and moved["due_at"] > t["due_at"]


async def asyncio_sleep_a_beat():
    import asyncio
    await asyncio.sleep(0.05)


async def test_when_the_pause_ends_and_the_customer_is_still_waiting_the_ai_takes_over_and_answers(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800040005"
    await after_ai_reply(world, shop, phone)
    await world.owner_replies_from_app(shop, phone, "I will get back to you")
    await world.drain(ENTRY)
    base = len(await sim_texts(world, shop, phone))
    await world.customer_says(shop, phone, "do you open on sunday?")
    await world.drain_for(1.5)                                           # paused: recorded, not answered (INV-10)
    assert len(await sim_texts(world, shop, phone)) == base
    async with world.rt.db.tenant(shop.business_id) as c:                # the owner never came back: the pause ends
        await c.execute("UPDATE conversations SET ai_paused_until = now() - interval '1 minute'")
        await c.execute("UPDATE agent_timers SET due_at = now() - interval '1 minute' WHERE kind='takeover_check' AND status='pending'")
    assert await agent_clock(world).tick_timers() == 1
    out = await settle(world, shop, phone, want=base + 1)
    assert len(out) == base + 1                                          # the waiting customer was answered
    async with world.rt.db.tenant(shop.business_id) as c:
        assert [r["outcome"] for r in await (await c.execute("SELECT outcome FROM agent_runs WHERE trigger='takeover'")).fetchall()] == ["replied"]


async def test_if_the_owner_answered_the_customer_the_ai_does_not_take_over(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800040006"
    await after_ai_reply(world, shop, phone)
    await world.owner_replies_from_app(shop, phone, "One moment")
    await world.drain(ENTRY)
    await world.customer_says(shop, phone, "do you open on sunday?")
    await world.drain_for(1.0)
    await world.owner_replies_from_app(shop, phone, "Yes, 10 to 8 on Sundays")        # the owner answered the question
    await world.drain(ENTRY)
    base = len(await sim_texts(world, shop, phone))
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE conversations SET ai_paused_until = now() - interval '1 minute'")
        await c.execute("UPDATE agent_timers SET due_at = now() - interval '1 minute' WHERE kind='takeover_check' AND status='pending'")
    await agent_clock(world).tick_timers()
    await world.drain_for(1.5)
    assert len(await sim_texts(world, shop, phone)) == base
    assert not await pending(world, shop, "takeover_check")


async def test_resuming_the_ai_while_a_customer_waits_takes_over_at_once(world, client):
    shop, tok = await owner(world, client, "Resume Shop")
    phone = "+919800040007"
    await after_ai_reply(world, shop, phone)
    await world.owner_replies_from_app(shop, phone, "I have this")
    await world.drain(ENTRY)
    base = len(await sim_texts(world, shop, phone))
    await world.customer_says(shop, phone, "do you open on sunday?")
    await world.drain_for(1.2)
    [conv] = await world.q(shop, "SELECT id FROM conversations")
    assert (await client.post(f"{V1}/conversations/{conv['id']}/resume", headers=hdr(tok))).status_code == 200
    await world.drain(ENTRY)
    [t] = await pending(world, shop, "takeover_check")
    assert t["reason"] == "customer_waiting" and t["due_at"] <= datetime.now(UTC) + timedelta(seconds=5)
    assert await agent_clock(world).tick_timers() == 1
    assert len(await settle(world, shop, phone, want=base + 1)) == base + 1


# ---------------------------------------------------------------- safety

async def test_a_timer_for_a_chat_the_ai_may_no_longer_answer_is_cancelled_and_nothing_is_sent(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800040008"
    await after_ai_reply(world, shop, phone)
    base = len(await sim_texts(world, shop, phone))
    await make_due(world, shop)
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE businesses SET ai_enabled = false WHERE id=%s", (shop.business_id,))
    assert await agent_clock(world).tick_timers() == 0
    await world.drain_for(1.0)
    assert len(await sim_texts(world, shop, phone)) == base
    [t] = await timers(world, shop, kind="follow_back", status="cancelled")
    assert t["cancel_reason"] == "ai_not_allowed"


async def test_timers_are_rows_so_a_restart_loses_none_and_the_sweep_rearms_missing_ones(world):
    shop, _ = await shop_with_catalog(world)
    await after_ai_reply(world, shop, "+919800040009")
    [t] = await pending(world, shop, "follow_back")
    assert await Scheduler(world.rt, "agent").tick_timers() == 0         # a fresh scheduler sees the same pending timer, not due yet
    async with world.rt.db.tenant(shop.business_id) as c:                # the timer rows are lost (a restore, a bug)
        await c.execute("DELETE FROM agent_timers")
    swept = await Reconciler(world.rt.db, timers=world.rt.timers, business_id=shop.business_id).sweep()
    assert swept["timers_planned"] >= 1
    [again] = await pending(world, shop, "follow_back")
    assert abs((again["due_at"] - t["due_at"]).total_seconds()) < 5       # the same answer, computed again from what the chat says


def test_only_the_agents_scheduler_runs_the_clock():
    assert "timers" in DUTIES["agent"] and "timers" in DUTIES["all"]
    assert "timers" not in DUTIES["web"] and "timers" not in DUTIES["gateway"]
    assert not any("nudge" in d for duties in DUTIES.values() for d in duties)
