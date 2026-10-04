"""Scheduler: leader election, daily summaries, nudges (at most one), retention."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from salesai.db import jsonb
from salesai.scheduler import LeaderLock, Scheduler
from tests.test_conversation_flow import shop_with_catalog
from tests.world import configure_fast, settle, sim_texts


async def test_only_one_scheduler_is_leader_at_a_time(rt):
    a, b = LeaderLock(rt.settings.system_database_url), LeaderLock(rt.settings.system_database_url)
    assert await a.try_acquire() is True
    assert await b.try_acquire() is False            # standby
    await a.release()
    assert await b.try_acquire() is True             # failover
    await b.release()


async def test_daily_summary_goes_to_the_owner_once_per_day(world):
    shop, _ = await shop_with_catalog(world)
    await configure_fast(world, shop, conv={"daily_summary_hour": 0})
    await world.customer_says(shop, "+919500000001", "silk saree price?")
    await settle(world, shop, "+919500000001")
    sch = Scheduler(world.rt)
    assert await sch.tick_daily_summaries() >= 1
    await world.drain_for(1.0)
    rows = [r for r in await world.sim_thread(shop.owner_phone, shop.business_phone) if r["template_name"] == "daily_summary"]
    assert len(rows) == 1 and "Conversations: 1" in rows[0]["body"]
    await sch.tick_daily_summaries()
    await world.drain_for(0.5)
    assert len([r for r in await world.sim_thread(shop.owner_phone, shop.business_phone) if r["template_name"] == "daily_summary"]) == 1   # deduped


async def test_a_quiet_interested_customer_gets_exactly_one_in_window_nudge(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919500000002"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    base = len(await sim_texts(world, shop, phone))
    async with world.rt.db.tenant(shop.business_id) as c:      # the customer went quiet five hours ago
        await c.execute("UPDATE conversations SET last_outbound_at = now() - interval '5 hours', last_inbound_at = now() - interval '5 hours 10 minutes'")
    sch = Scheduler(world.rt)
    assert await sch.tick_nudges() == 1
    await settle(world, shop, phone, want=base + 1)
    texts = await sim_texts(world, shop, phone)
    assert len(texts) == base + 1 and not re.search(r"\d{3,}|₹", texts[-1])        # a gentle check-in, never a price
    assert await sch.tick_nudges() == 0                                              # at most one
    await world.drain_for(0.5)
    assert len(await sim_texts(world, shop, phone)) == base + 1


async def test_nudges_respect_stopped_selling_pauses_and_the_window(world):
    shop, _ = await shop_with_catalog(world)
    for i, (cond, desc) in enumerate([("selling_stopped = true", "stopped"), ("ai_paused_until = now() + interval '1 hour'", "paused"),
                                      ("last_inbound_at = now() - interval '23 hours'", "window")]):
        phone = f"+91950000{i + 10:04d}"
        await world.customer_says(shop, phone, "silk saree price?")
        await settle(world, shop, phone)
        async with world.rt.db.tenant(shop.business_id) as c:
            await c.execute(f"UPDATE conversations SET last_outbound_at = now() - interval '5 hours', {cond} WHERE customer_id=(SELECT id FROM customers WHERE wa_id=%s)", (phone.lstrip("+"),))  # noqa: S608
            if desc != "window":
                await c.execute("UPDATE conversations SET last_inbound_at = now() - interval '5 hours 10 minutes' WHERE customer_id=(SELECT id FROM customers WHERE wa_id=%s)", (phone.lstrip("+"),))
    assert await Scheduler(world.rt).tick_nudges() == 0


async def test_retention_purges_old_data_only(world):
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO webhook_events (signature_valid, payload, received_at) VALUES (true, '{}', now() - interval '40 days')")
        await c.execute("INSERT INTO webhook_events (signature_valid, payload) VALUES (true, '{\"keep\": 1}')")
    out = await Scheduler(world.rt).tick_retention()
    assert out["webhook_events"] >= 1
    async with world.rt.db.system_tx() as c:
        assert (await (await c.execute("SELECT count(*) AS n FROM webhook_events WHERE payload @> '{\"keep\": 1}'")).fetchone())["n"] >= 1


async def test_dead_letters_raise_an_operator_alert(world):
    from salesai.queue.base import JobSpec
    q = f"inbound.events"
    await world.rt.queue.publish(JobSpec(q, "no_such_kind", {}, max_attempts=1))
    await world.drain([q])
    async with world.rt.db.system_tx() as c:
        n = (await (await c.execute("SELECT count(*) AS n FROM jobs WHERE queue=%s AND status='dead'", (q,))).fetchone())["n"]
    assert n >= 1
    await Scheduler(world.rt).tick_gauges()
    async with world.rt.db.system_tx() as c:
        assert await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='dead_letter' AND resolved_at IS NULL")).fetchone()
