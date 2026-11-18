"""Knowing when a part is down (ADR 0046): heartbeats, the part-down and queue-stall alerts, and the outside dead-man's switch."""
from __future__ import annotations

import pytest

from salesai.heartbeat import Heartbeat, unit_name
from salesai.queue.base import JobSpec
from salesai.scheduler import Scheduler
from tests.fake_hook import FakeHook


async def _open_alert(rt, kind: str, key: str):
    async with rt.db.system_tx() as c:
        return await (await c.execute(
            "SELECT * FROM operator_alerts WHERE kind=%s AND dedup_key=%s AND resolved_at IS NULL", (kind, key))).fetchone()


async def _set_last_seen(rt, unit: str, seconds_ago: int) -> None:
    async with rt.db.system_tx() as c:
        await c.execute(
            "INSERT INTO heartbeats (unit, role, instance, last_seen_at) VALUES (%s, 'test', 'test', now() - make_interval(secs => %s)) "
            "ON CONFLICT (unit) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at", (unit, seconds_ago))


def test_each_process_has_a_readable_unit_name(rt):
    s = rt.settings
    assert unit_name("ingress", s) == "gateway receive"
    assert unit_name("api", s) == "web api"
    assert unit_name("admin", s) == "admin api"
    assert unit_name("worker", s.model_copy(update={"worker_queues": "inbound.events"})) == "gateway inbound"
    assert unit_name("worker", s.model_copy(update={"worker_queues": "outbound.actions"})) == "gateway send"
    agent = "conversation.events,conversation.turns,owner.notifications,platform.events"
    assert unit_name("worker", s.model_copy(update={"worker_queues": agent})) == "agent worker"
    assert unit_name("scheduler", s.model_copy(update={"scheduler_component": "web"})) == "web scheduler"
    assert unit_name("all", s) == "all-in-one"


async def test_a_heartbeat_records_the_unit_and_keeps_it_fresh(rt):
    hb = Heartbeat(rt.db, "test beating unit", "worker")
    await hb.beat()
    async with rt.db.system_tx() as c:
        row = await (await c.execute("SELECT * FROM heartbeats WHERE unit='test beating unit'")).fetchone()
    assert row is not None and row["role"] == "worker"


async def test_a_silent_customer_path_part_raises_a_critical_alert_that_resolves_when_it_beats_again(rt):
    await _set_last_seen(rt, "agent worker", 600)
    await Scheduler(rt, "agent").tick_parts()
    alert = await _open_alert(rt, "part_down", "agent worker")
    assert alert is not None and alert["severity"] == "critical"

    await _set_last_seen(rt, "agent worker", 0)
    await Scheduler(rt, "agent").tick_parts()
    assert await _open_alert(rt, "part_down", "agent worker") is None


async def test_a_silent_back_office_part_is_high_and_one_alert_however_often_it_is_checked(rt):
    await _set_last_seen(rt, "admin api", 600)
    for _ in range(3):
        await Scheduler(rt, "web").tick_parts()
    async with rt.db.system_tx() as c:
        rows = await (await c.execute(
            "SELECT severity FROM operator_alerts WHERE kind='part_down' AND dedup_key='admin api' AND resolved_at IS NULL")).fetchall()
    assert [r["severity"] for r in rows] == ["high"]
    await _set_last_seen(rt, "admin api", 0)
    await Scheduler(rt, "web").tick_parts()


async def test_a_fresh_part_and_a_long_retired_part_raise_nothing(rt):
    await _set_last_seen(rt, "test fresh unit", 5)
    await _set_last_seen(rt, "test retired unit", 3 * 24 * 3600)
    await Scheduler(rt, "gateway").tick_parts()
    assert await _open_alert(rt, "part_down", "test fresh unit") is None
    assert await _open_alert(rt, "part_down", "test retired unit") is None


async def test_a_queue_whose_oldest_job_waits_too_long_raises_an_alert_that_clears_when_it_catches_up(world):
    rt = world.rt
    q = "platform.events"
    await rt.queue.publish(JobSpec(q, "no_such_kind", {"t": "stall"}, max_attempts=1))
    sched = Scheduler(rt, "gateway")
    sched.stall_after_s = -1                      # any due job counts as waiting too long
    await sched.tick_gauges()
    alert = await _open_alert(rt, "queue_stalled", q)
    assert alert is not None and alert["severity"] == "high"

    await world.drain([q])                        # the consumer catches up (the bad job is dead-lettered)
    sched.stall_after_s = 3600
    await sched.tick_gauges()
    assert await _open_alert(rt, "queue_stalled", q) is None


async def test_customer_queues_stall_as_critical(world):
    rt = world.rt
    q = "conversation.events"
    await rt.queue.publish(JobSpec(q, "no_such_kind", {"t": "stall-critical"}, max_attempts=1))
    sched = Scheduler(rt, "agent")
    sched.stall_after_s = -1
    await sched.tick_gauges()
    alert = await _open_alert(rt, "queue_stalled", q)
    assert alert is not None and alert["severity"] == "critical"
    await world.drain([q])
    sched.stall_after_s = 3600
    await sched.tick_gauges()


async def test_the_web_scheduler_pings_the_outside_checker_after_each_alert_run(rt):
    hook = FakeHook()
    hook.start()
    try:
        sched = Scheduler(rt, "web")
        sched.heartbeat_url = hook.url
        assert await sched.ping_outside() is True
        assert hook.received and hook.received[-1]["service"] == "chatdeal"
        hook.status = 500
        assert await sched.ping_outside() is False          # a failing checker is logged, never raised
    finally:
        hook.stop()


@pytest.mark.parametrize("component", ["agent", "gateway"])
async def test_only_the_web_scheduler_pings_the_outside_checker(rt, component):
    sched = Scheduler(rt, component)
    sched.heartbeat_url = "http://127.0.0.1:9/never"
    assert await sched.ping_outside() is False
