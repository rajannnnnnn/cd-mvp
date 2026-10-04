"""Operator alerts are pushed to the platform team (webhook and WhatsApp), once, with reminders for unresolved critical ones."""
from __future__ import annotations

import pytest

from salesai.modules.alerting import AlertRouter
from salesai.modules.alerting.router import WhatsAppSink
from salesai.modules.alerting.webhook_sink import WebhookSink
from tests.fake_hook import FakeHook
from tests.world import wa_id


@pytest.fixture
def hook():
    h = FakeHook()
    h.start()
    yield h
    h.stop()


async def _alert(world, shop, severity="warning", kind="send_failed", message="Outbound message failed: window_closed"):
    async with world.rt.db.system_tx() as c:
        return (await (await c.execute(
            "INSERT INTO operator_alerts (business_id, severity, kind, message) VALUES (%s,%s,%s,%s) RETURNING id", (shop.business_id, severity, kind, message))).fetchone())["id"]


async def _row(world, aid):
    async with world.rt.db.system_tx() as c:
        return await (await c.execute("SELECT * FROM operator_alerts WHERE id=%s", (aid,))).fetchone()


async def test_alert_reaches_webhook_once_and_names_the_business(world, hook):
    shop = await world.make_shop("Sharma Sarees")
    router = AlertRouter(world.rt.db, [WebhookSink(hook.url)])
    aid = await _alert(world, shop)
    assert await router.dispatch() >= 1
    mine = [m for m in hook.received if m["id"] == aid]
    assert len(mine) == 1 and "Sharma Sarees" in mine[0]["text"] and mine[0]["severity"] == "warning" and "window_closed" in mine[0]["text"]
    await router.dispatch()                                                           # already notified: not sent again
    assert len([m for m in hook.received if m["id"] == aid]) == 1
    assert (await _row(world, aid))["notified_at"] is not None


async def test_alert_reaches_the_team_on_whatsapp(world):
    shop = await world.make_shop("Gupta Mobile")
    team = "+919999900000"
    router = AlertRouter(world.rt.db, [WhatsAppSink(world.rt.channels, [team])])
    aid = await _alert(world, shop, "critical", "number_disconnected", "WhatsApp number disconnected")
    await router.dispatch()
    async with world.rt.db.system_tx() as c:
        msgs = await (await c.execute("SELECT * FROM sim_messages WHERE phone=%s AND template_name='operator_alert'", (wa_id(team),))).fetchall()
    assert any("Gupta Mobile" in (m["body"] or "") and "CRITICAL" in (m["body"] or "") for m in msgs)
    assert (await _row(world, aid))["notified_at"] is not None


async def test_minimum_severity_is_respected(world, hook):
    shop = await world.make_shop("Quiet Shop")
    router = AlertRouter(world.rt.db, [WebhookSink(hook.url)], min_severity="critical")
    low = await _alert(world, shop, "warning")
    high = await _alert(world, shop, "critical")
    await router.dispatch()
    ids = {m["id"] for m in hook.received}
    assert high in ids and low not in ids
    assert (await _row(world, low))["notified_at"] is None                            # stays visible in the console


async def test_failing_sink_is_retried_then_given_up_without_blocking_others(world, hook):
    shop = await world.make_shop("Retry Shop")
    bad = WebhookSink("http://127.0.0.1:9/never")                                      # nothing listens here
    router = AlertRouter(world.rt.db, [bad], max_attempts=2)
    aid = await _alert(world, shop)
    await router.dispatch()
    row = await _row(world, aid)
    assert row["notify_attempts"] == 1 and row["notified_at"] is None and row["notify_error"]
    await router.dispatch()
    await router.dispatch()                                                            # attempts exhausted: no more tries
    assert (await _row(world, aid))["notify_attempts"] == 2
    good = AlertRouter(world.rt.db, [bad, WebhookSink(hook.url)])                      # one good sink is enough to mark it notified
    aid2 = await _alert(world, shop)
    await good.dispatch()
    assert (await _row(world, aid2))["notified_at"] is not None


async def test_unresolved_critical_alerts_are_repeated_and_resolved_ones_are_not(world, hook):
    shop = await world.make_shop("Reminder Shop")
    router = AlertRouter(world.rt.db, [WebhookSink(hook.url)], reminder_minutes=60)
    aid = await _alert(world, shop, "critical", "dead_letter", "3 dead-lettered job(s)")
    await router.dispatch()
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE operator_alerts SET notified_at = now() - interval '2 hours' WHERE id=%s", (aid,))
    await router.dispatch()
    assert len([m for m in hook.received if m["id"] == aid]) == 2                      # reminder
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE operator_alerts SET notified_at = now() - interval '2 hours', resolved_at = now() WHERE id=%s", (aid,))
    await router.dispatch()
    assert len([m for m in hook.received if m["id"] == aid]) == 2                      # resolved: silence


async def test_alert_text_never_carries_customer_content(world, hook):
    shop = await world.make_shop("Private Shop")
    router = AlertRouter(world.rt.db, [WebhookSink(hook.url)])
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO operator_alerts (business_id, severity, kind, message, detail) VALUES (%s,'warning','send_failed','Outbound message failed: x', %s::jsonb)",
                        (shop.business_id, '{"detail": "secret customer text 9876543210"}'))
    await router.dispatch()
    assert all("secret customer text" not in str(m) for m in hook.received)
