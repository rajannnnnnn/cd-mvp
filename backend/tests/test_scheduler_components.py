"""One scheduler per component (ADR 0025): each part keeps its own timer, so a part can be deployed, restarted or scaled on
its own. The web part runs billing and alert delivery; the agent runs follow-ups and the recovery sweep; the gateway keeps its
own raw-request log tidy. All three move the outbox along (safe to run in parallel: they skip rows another has locked).
`all` is the development combination and does everything. Each component holds its own leader lock, and cleans up only the
data it owns, so a component never needs rights over another's tables."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from salesai.scheduler import DUTIES, LeaderLock, Scheduler
from tests.test_conversation_flow import shop_with_catalog
from tests.test_secrets import settings as make_settings

OLD = datetime.now(UTC) - timedelta(days=400)


def test_each_component_has_only_its_own_duties():
    assert set(DUTIES) == {"all", "web", "gateway", "agent"}
    assert {"billing", "alerts", "deletions"} <= DUTIES["web"] and not ({"timers", "recovery"} & DUTIES["web"])
    assert {"timers", "recovery"} <= DUTIES["agent"] and not ({"billing", "alerts"} & DUTIES["agent"])
    assert not ({"billing", "alerts", "timers", "recovery"} & DUTIES["gateway"])
    assert DUTIES["all"] == DUTIES["web"] | DUTIES["agent"] | DUTIES["gateway"]              # the development combination loses nothing
    for d in DUTIES.values():
        assert {"gauges", "parts", "retention"} <= d                                         # every component watches its queues, the parts' heartbeats, and tidies up


@pytest.mark.parametrize("component", ["web", "agent", "gateway", "all"])
async def test_a_scheduler_runs_exactly_its_components_duties(rt, monkeypatch, component):
    called: set[str] = set()

    def spy(name: str):
        async def _go(*a, **k):
            called.add(name)
            return 0
        return _go

    sch = Scheduler(rt, component)
    for tick, name in (("tick_timers", "timers"), ("tick_gauges", "gauges"), ("tick_parts", "parts"),
                       ("tick_retention", "retention"), ("tick_deletions", "deletions")):
        monkeypatch.setattr(sch, tick, spy(name))
    monkeypatch.setattr(rt.billing, "run_cycle", spy("billing"))                              # these would act on every shop in the database
    monkeypatch.setattr(rt.alerts, "dispatch", spy("alerts"))
    monkeypatch.setattr(rt.reconciler, "sweep", spy("recovery"))                              # the agent's recovery sweep
    await sch.tick_slow(tidy=True)
    assert called == DUTIES[component]                                                       # all of its own duties, and nothing of anyone else's
    called.clear()
    await sch.tick_slow(tidy=False)                                                          # retention runs hourly, the rest every pass
    assert called == DUTIES[component] - {"retention"}


async def test_components_hold_separate_leader_locks_and_the_same_component_stands_by(rt):
    web, agent, web2 = (LeaderLock(rt.settings.system_database_url, Scheduler(rt, c).lock_id) for c in ("web", "agent", "web"))
    try:
        assert await web.try_acquire() is True
        assert await agent.try_acquire() is True                                              # a different component: independent
        assert await web2.try_acquire() is False                                              # a second web scheduler stands by
    finally:
        for lock in (web, agent, web2):
            await lock.release()


def test_the_component_is_configuration_and_unknown_ones_are_refused():
    assert make_settings(SCHEDULER_COMPONENT="agent").scheduler_component == "agent"
    assert make_settings().scheduler_component == "all"
    with pytest.raises(ValidationError):
        make_settings(SCHEDULER_COMPONENT="billing")


async def _x(world, shop, sql: str, *args) -> None:
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute(sql, args or None)


async def test_each_component_tidies_only_what_it_owns(world, monkeypatch):
    rt = world.rt
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919600000501", "hi")
    await world.drain(["inbound.events", "conversation.events"])
    [conv] = await world.q(shop, "SELECT id FROM conversations ORDER BY created_at DESC LIMIT 1")
    monkeypatch.setattr(rt.settings, "chat_retention_days", 365)
    async with rt.db.system_tx() as c:
        await c.execute("INSERT INTO otp_challenges (phone, code_hash, expires_at, created_at) VALUES ('+910000000501','x', now(), %s)", (OLD,))
        await c.execute("INSERT INTO webhook_events (signature_valid, payload, raw_body, received_at) VALUES (true,'{\"t\":1}',%s,%s)", (b"ancient-501", OLD))
    await _x(world, shop, "INSERT INTO messages (business_id, conversation_id, direction, sender, body, created_at) VALUES (%s,%s,'in','customer','ancient-501',%s)",
             shop.business_id, conv["id"], OLD)

    async def counts() -> dict[str, int]:
        async with rt.db.system_tx() as c:
            otp = (await (await c.execute("SELECT count(*) AS n FROM otp_challenges WHERE phone='+910000000501'")).fetchone())["n"]
            raw = (await (await c.execute("SELECT count(*) AS n FROM webhook_events WHERE raw_body=%s", (b"ancient-501",))).fetchone())["n"]
        msg = len(await world.q(shop, "SELECT 1 FROM messages WHERE body='ancient-501'"))
        return {"otp": otp, "raw": raw, "msg": msg}

    assert await counts() == {"otp": 1, "raw": 1, "msg": 1}
    await Scheduler(rt, "web").tick_retention()
    assert await counts() == {"otp": 0, "raw": 1, "msg": 1}                                  # web: sign-in data only
    await Scheduler(rt, "gateway").tick_retention()
    assert await counts() == {"otp": 0, "raw": 0, "msg": 1}                                  # gateway: its raw request log only
    await Scheduler(rt, "agent").tick_retention()
    assert await counts() == {"otp": 0, "raw": 0, "msg": 0}                                  # agent: conversations


async def test_the_shared_plumbing_is_tidied_by_every_component(rt):
    for component in ("web", "gateway", "agent"):
        async with rt.db.system_tx() as c:
            await c.execute("INSERT INTO outbox (event_type, payload, published_at) VALUES ('turn.decided','{}', %s)", (OLD,))
        await Scheduler(rt, component).tick_retention()
        async with rt.db.system_tx() as c:
            left = (await (await c.execute("SELECT count(*) AS n FROM outbox WHERE published_at = %s", (OLD,))).fetchone())["n"]
        assert left == 0, component
