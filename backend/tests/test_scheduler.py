"""Scheduler: leader election and retention. (The agent's timers: tests/test_agent_timers.py.)"""
from __future__ import annotations

from salesai.scheduler import LeaderLock, Scheduler


async def test_only_one_scheduler_is_leader_at_a_time(rt):
    a, b = LeaderLock(rt.settings.system_database_url), LeaderLock(rt.settings.system_database_url)
    assert await a.try_acquire() is True
    assert await b.try_acquire() is False            # standby
    await a.release()
    assert await b.try_acquire() is True             # failover
    await b.release()


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
    q = "inbound.events"
    await world.rt.queue.publish(JobSpec(q, "no_such_kind", {}, max_attempts=1))
    await world.drain([q])
    assert len(await world.rt.queue.dead_letters(q)) >= 1
    await Scheduler(world.rt).tick_gauges()
    async with world.rt.db.system_tx() as c:
        assert await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='dead_letter' AND resolved_at IS NULL")).fetchone()
