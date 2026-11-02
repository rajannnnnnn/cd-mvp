"""ADR 0029: platform alerts reach the founder by email with four severities, grouped and rate-limited so the inbox stays
readable, and they never reach a subscriber. A real SMTP conversation against a local stand-in server."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import salesai
from salesai.alerts import raise_alert
from salesai.config import Settings
from salesai.modules.alerting import AlertRouter
from salesai.modules.alerting.email_sink import EmailSink
from salesai.modules.alerting.router import WhatsAppSink
from salesai.modules.alerting.webhook_sink import WebhookSink
from tests.fake_hook import FakeHook
from tests.fake_smtp import FakeSmtp
from tests.world import wa_id

FOUNDER = "founder@chatdeal.example"


@pytest.fixture
async def smtp():
    s = FakeSmtp()
    await s.start()
    yield s
    await s.stop()


@pytest.fixture(autouse=True)
async def fresh(world):
    """The database is shared across the suite: start each test with no other alerts for the router to pick up."""
    async with world.rt.db.system_tx() as c:
        await c.execute("DELETE FROM operator_alerts")


def mail(smtp: FakeSmtp, **kw) -> EmailSink:
    return EmailSink("127.0.0.1", smtp.port, sender="alerts@chatdeal.example", to=[FOUNDER], security="none", **kw)


def router(world, sinks, **kw) -> AlertRouter:
    return AlertRouter(world.rt.db, sinks, **kw)


async def raised(world, shop, severity, kind="send_failed", message="Outbound message failed: window_closed", **kw) -> int:
    async with world.rt.db.system_tx() as c:
        return await raise_alert(c, severity, kind, message, business_id=shop.business_id if shop else None, **kw)


async def row(world, aid):
    async with world.rt.db.system_tx() as c:
        return await (await c.execute("SELECT * FROM operator_alerts WHERE id=%s", (aid,))).fetchone()


async def age(world, aid, **cols):
    """Pretend time has passed: move the named timestamp columns back by an interval, e.g. notified_at='2 hours'."""
    async with world.rt.db.system_tx() as c:
        for col, interval in cols.items():
            await c.execute(f"UPDATE operator_alerts SET {col} = {col} - %s::interval WHERE id=%s", (interval, aid))


async def resolve(world, aid):
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE operator_alerts SET resolved_at = now() WHERE id=%s", (aid,))


# ---------------------------------------------------------------- the same cause is one alert with a count

async def test_the_same_cause_is_one_alert_with_a_count(world):
    shop, other = await world.make_shop("Count Shop"), await world.make_shop("Other Shop")
    a = await raised(world, shop, "medium", message="Outbound message failed: window_closed")
    b = await raised(world, shop, "medium", message="Outbound message failed: rate_limited")
    assert a == b
    r = await row(world, a)
    assert r["occurrences"] == 2 and r["message"].endswith("rate_limited")      # the latest wording wins
    assert await raised(world, other, "medium") != a                            # another shop is another alert
    assert await raised(world, shop, "medium", dedup_key="queue-x") != a        # another cause of the same kind too
    await resolve(world, a)
    assert await raised(world, shop, "medium") != a                             # once resolved, a new occurrence is a new alert


async def test_an_alert_can_only_get_more_severe_and_is_announced_again_when_it_does(world):
    shop = await world.make_shop("Escalating Shop")
    a = await raised(world, shop, "high", "quality_drop", "Quality is now yellow")
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE operator_alerts SET notified_at = now(), notified_occurrences = occurrences WHERE id=%s", (a,))
    assert await raised(world, shop, "medium", "quality_drop", "Quality is still yellow") == a
    assert (await row(world, a))["severity"] == "high" and (await row(world, a))["notified_at"] is not None
    assert await raised(world, shop, "critical", "quality_drop", "Quality is now red") == a
    r = await row(world, a)
    assert r["severity"] == "critical" and r["notified_at"] is None             # worse than what the founder was told: tell again


async def test_only_the_four_severities_exist(world):
    shop = await world.make_shop("Strict Shop")
    for legacy in ("info", "warning", "error", ""):
        with pytest.raises(ValueError):
            await raised(world, shop, legacy)
    async with world.rt.db.system_tx() as c:
        with pytest.raises(Exception, match="severity"):                          # and the database agrees
            await c.execute("INSERT INTO operator_alerts (severity, kind, message) VALUES ('warning','x','y')")


# ---------------------------------------------------------------- Critical: immediately, then every 30 minutes, then a recovery notice

async def test_critical_goes_out_at_once_repeats_until_resolved_and_ends_with_a_recovery_notice(world, smtp):
    shop = await world.make_shop("Crit Shop")
    r = router(world, [mail(smtp)])
    aid = await raised(world, shop, "critical", "number_disconnected", "WhatsApp number +919800000000 disconnected: banned")
    await r.dispatch()
    assert len(smtp.messages) == 1
    assert smtp.subjects()[0].startswith("[CRITICAL]") and "Crit Shop" in smtp.subjects()[0]
    body = smtp.bodies()[0]
    assert "disconnected" in body and "Operator console" in body and f"#{aid}" in body     # what happened and where to look
    assert smtp.envelopes[0]["to"] == [FOUNDER]
    await r.dispatch()
    assert len(smtp.messages) == 1                                              # not again straight away
    await age(world, aid, notified_at="31 minutes")
    await r.dispatch()
    assert len(smtp.messages) == 2                                              # a reminder every 30 minutes
    await resolve(world, aid)
    await r.dispatch()
    assert len(smtp.messages) == 3 and "RESOLVED" in smtp.subjects()[2] and "Crit Shop" in smtp.subjects()[2]
    await age(world, aid, notified_at="2 hours")
    await r.dispatch()
    assert len(smtp.messages) == 3                                              # resolved: silence, and the recovery notice is sent once


# ---------------------------------------------------------------- High: immediately, 30-minute cooldown per cause

async def test_high_goes_out_at_once_then_waits_out_its_cooldown_and_only_repeats_on_news(world, smtp):
    shop = await world.make_shop("High Shop")
    r = router(world, [mail(smtp)])
    aid = await raised(world, shop, "high", "number_disconnected", "Disconnected")
    await r.dispatch()
    assert len(smtp.messages) == 1 and smtp.subjects()[0].startswith("[HIGH]")
    await raised(world, shop, "high", "number_disconnected", "Disconnected")        # happens again straight away
    await r.dispatch()
    assert len(smtp.messages) == 1                                              # inside the cooldown: counted, not mailed
    await age(world, aid, notified_at="31 minutes")
    await r.dispatch()
    assert len(smtp.messages) == 2 and "2 times" in smtp.bodies()[1]           # cooldown over and there is news: one mail with the count
    await age(world, aid, notified_at="31 minutes")
    await r.dispatch()
    assert len(smtp.messages) == 2                                              # nothing new since: no repeat of a High
    await resolve(world, aid)
    await r.dispatch()
    assert "RESOLVED" in smtp.subjects()[-1]


# ---------------------------------------------------------------- Medium: an hourly rollup; Low: a daily digest

async def test_medium_alerts_arrive_as_one_hourly_rollup(world, smtp):
    a, b = await world.make_shop("Rollup One"), await world.make_shop("Rollup Two")
    r = router(world, [mail(smtp)])
    ids = [await raised(world, a, "medium", "send_failed", "Sends are failing"), await raised(world, b, "medium", "llm_errors", "Model errors rising")]
    await r.dispatch()
    assert smtp.messages == []                                                  # the first hour is spent gathering
    for i in ids:
        await age(world, i, created_at="61 minutes")
    await r.dispatch()
    assert len(smtp.messages) == 1 and smtp.subjects()[0].startswith("[MEDIUM]")
    assert "Rollup One" in smtp.bodies()[0] and "Rollup Two" in smtp.bodies()[0]       # two alerts, one email
    await r.dispatch()
    assert len(smtp.messages) == 1
    late = await raised(world, a, "medium", "queue_age", "Queue is old")
    await age(world, late, created_at="10 minutes")
    await r.dispatch()
    assert len(smtp.messages) == 1                                              # at most one rollup per hour
    await age(world, ids[0], notified_at="61 minutes")
    await age(world, ids[1], notified_at="61 minutes")
    await r.dispatch()
    assert len(smtp.messages) == 2 and "Queue is old" in smtp.bodies()[1] and "Sends are failing" not in smtp.bodies()[1]


async def test_low_alerts_arrive_as_one_daily_digest(world, smtp):
    shop = await world.make_shop("Digest Shop")
    r = router(world, [mail(smtp)])
    aid = await raised(world, shop, "low", "subscription_lapsed", "Subscription lapsed: trial ended")
    await age(world, aid, created_at="23 hours")
    await r.dispatch()
    assert smtp.messages == []
    await age(world, aid, created_at="2 hours")
    await r.dispatch()
    assert len(smtp.messages) == 1 and smtp.subjects()[0].startswith("[LOW]") and "Digest Shop" in smtp.bodies()[0]


async def test_medium_and_low_never_go_to_the_chat_sinks(world, smtp):
    shop = await world.make_shop("Quiet Chat Shop")
    hook = FakeHook()
    hook.start()
    try:
        r = router(world, [WebhookSink(hook.url), mail(smtp)])
        for sev in ("low", "medium"):
            aid = await raised(world, shop, sev, f"k_{sev}", "m")
            await age(world, aid, created_at="2 days")
        await r.dispatch()
        assert hook.received == [] and len(smtp.messages) == 2
    finally:
        hook.stop()


async def test_each_severity_uses_the_sinks_meant_for_it(world, smtp):
    shop = await world.make_shop("Routing Shop")
    hook = FakeHook()
    hook.start()
    team = "+919999900001"
    try:
        r = router(world, [mail(smtp), WebhookSink(hook.url), WhatsAppSink(world.rt.channels, [team])])
        high = await raised(world, shop, "high", "quality_drop", "Quality dropped")
        crit = await raised(world, shop, "critical", "dead_letter", "Jobs are dead", dedup_key="q")
        await r.dispatch()
        assert {m["id"] for m in hook.received} == {high, crit}                  # the webhook takes High and Critical
        async with world.rt.db.system_tx() as c:
            wa = await (await c.execute("SELECT body FROM sim_messages WHERE phone=%s AND template_name='operator_alert'", (wa_id(team),))).fetchall()
        assert len(wa) == 1 and "CRITICAL" in wa[0]["body"]                       # WhatsApp is for Critical only
        assert len(smtp.messages) == 2                                           # and email takes both
    finally:
        hook.stop()


async def test_minimum_severity_silences_the_lower_ones(world, smtp):
    shop = await world.make_shop("Floor Shop")
    r = router(world, [mail(smtp)], min_severity="high")
    low = await raised(world, shop, "low", "a", "m")
    med = await raised(world, shop, "medium", "b", "m")
    high = await raised(world, shop, "high", "c", "m")
    for i in (low, med):
        await age(world, i, created_at="3 days")
    await r.dispatch()
    assert len(smtp.messages) == 1 and "[HIGH]" in smtp.subjects()[0]
    assert (await row(world, low))["notified_at"] is None and (await row(world, med))["notified_at"] is None      # still on the console
    assert (await row(world, high))["notified_at"] is not None


# ---------------------------------------------------------------- failures

async def test_a_mail_server_that_refuses_is_retried_then_given_up_and_the_webhook_still_covers(world, smtp):
    shop = await world.make_shop("Refused Shop")
    smtp.refuse = True
    r = router(world, [mail(smtp)], max_attempts=2)
    aid = await raised(world, shop, "high", "number_disconnected", "Disconnected")
    await r.dispatch()
    rr = await row(world, aid)
    assert rr["notify_attempts"] == 1 and rr["notified_at"] is None and "email" in (rr["notify_error"] or "")
    await r.dispatch()
    await r.dispatch()
    assert (await row(world, aid))["notify_attempts"] == 2                      # attempts exhausted: no more tries
    hook = FakeHook()
    hook.start()
    try:
        both = router(world, [mail(smtp), WebhookSink(hook.url)])
        aid2 = await raised(world, shop, "high", "other_cause", "Another")
        await both.dispatch()
        assert (await row(world, aid2))["notified_at"] is not None              # one working path is enough to count as told
    finally:
        hook.stop()


async def test_a_failed_digest_is_retried_not_lost(world, smtp):
    shop = await world.make_shop("Digest Retry Shop")
    aid = await raised(world, shop, "medium", "send_failed", "Sends failing")
    await age(world, aid, created_at="2 hours")
    smtp.refuse = True
    r = router(world, [mail(smtp)])
    await r.dispatch()
    assert smtp.messages == [] and (await row(world, aid))["notified_at"] is None
    smtp.refuse = False
    await r.dispatch()
    assert len(smtp.messages) == 1 and (await row(world, aid))["notified_at"] is not None


# ---------------------------------------------------------------- what the email says, and never says

async def test_the_email_names_the_shop_the_cause_and_the_count_and_carries_no_customer_content(world, smtp):
    shop = await world.make_shop("Private Shop")
    aid = await raised(world, shop, "high", "send_failed", "Outbound message failed: window_closed",
                       detail={"detail": "secret customer text 9876543210", "floor": 4200})
    await raised(world, shop, "high", "send_failed", "Outbound message failed: window_closed")
    await router(world, [mail(smtp)]).dispatch()
    text = smtp.subjects()[0] + smtp.bodies()[0]
    assert "Private Shop" in text and "window_closed" in text and "2 times" in text and f"#{aid}" in text
    assert "secret customer text" not in text and "9876543210" not in text and "4200" not in text


async def test_hostile_text_cannot_inject_headers_or_recipients(world, smtp):
    shop = await world.make_shop("Evil\r\nBcc: attacker@evil.example")
    await raised(world, shop, "high", "send_failed", "boom\r\nBcc: attacker@evil.example\r\n\r\nhello")
    await router(world, [mail(smtp)]).dispatch()
    assert len(smtp.messages) == 1 and smtp.envelopes[0]["to"] == [FOUNDER]
    assert all(m["Bcc"] is None for m in smtp.messages)


async def test_smtp_login_is_used_when_configured(world):
    smtp = FakeSmtp(advertise_auth=True)
    await smtp.start()
    try:
        shop = await world.make_shop("Login Shop")
        sink = EmailSink("127.0.0.1", smtp.port, sender="alerts@chatdeal.example", to=[FOUNDER], security="none",
                         username="mailer", password="s3cret")
        await raised(world, shop, "high", "x", "m")
        await router(world, [sink]).dispatch()
        assert smtp.logins == [("mailer", "s3cret")] and len(smtp.messages) == 1
    finally:
        await smtp.stop()


# ---------------------------------------------------------------- alerts never reach a subscriber

async def test_a_shops_owner_never_receives_a_platform_alert(world, smtp):
    shop = await world.make_shop("Subscriber Shop")
    for sev, kind in (("critical", "number_disconnected"), ("high", "quality_drop"), ("medium", "send_failed"), ("low", "support_ticket")):
        aid = await raised(world, shop, sev, kind, f"{sev} for Subscriber Shop")
        await age(world, aid, created_at="3 days")
    team = "+919999900002"
    await router(world, [mail(smtp), WhatsAppSink(world.rt.channels, [team])]).dispatch()
    assert smtp.messages and all(e["to"] == [FOUNDER] for e in smtp.envelopes)
    async with world.rt.db.system_tx() as c:
        sent = await (await c.execute("SELECT phone FROM sim_messages WHERE template_name='operator_alert' AND body LIKE '%Subscriber Shop%'")).fetchall()
    async with world.rt.db.tenant(shop.business_id) as c:
        owner_rows = await (await c.execute("SELECT 1 FROM owner_messages")).fetchall()
    assert {s["phone"] for s in sent} <= {wa_id(team)} and wa_id(shop.owner_phone) not in {s["phone"] for s in sent}
    assert owner_rows == []                                                      # and nothing was queued for the owner either
    assert shop.owner_phone not in "".join(smtp.bodies())


def test_the_alert_path_cannot_even_look_up_a_subscribers_address():
    """Structure, not just behaviour: the SQL in the alerting code touches only the alert table and the shop's name, so
    there is nowhere in it to find an owner's number; recipients come only from configuration."""
    import ast
    root = Path(salesai.__file__).parent
    files = sorted((root / "modules" / "alerting").glob("*.py")) + [root / "alerts.py"]
    table = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_]+)", re.I)
    seen: set[str] = set()
    for f in files:
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", node.value):
                seen |= {t.lower() for t in table.findall(node.value)} - {"set"}     # `DO UPDATE SET` is not a table
    assert seen == {"operator_alerts", "businesses"}, seen
    for f in files:
        text = f.read_text()
        assert "business_users" not in text and "owner_phone" not in text and "display_phone" not in text, f.name


# ---------------------------------------------------------------- configuration

def _settings(**kw) -> Settings:
    base = dict(role="all", master_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", jwt_secret="j" * 32, otp_secret="o" * 32,
                meta_app_secret="m" * 12, meta_verify_token="v" * 12)
    return Settings(**{**base, **kw})


def test_email_settings_are_validated_at_startup():
    ok = _settings(alert_email_to=f"{FOUNDER}, second@chatdeal.example", smtp_host="smtp.example", alert_email_from="alerts@chatdeal.example")
    assert ok.alert_emails == [FOUNDER, "second@chatdeal.example"]
    with pytest.raises(ValueError, match="SMTP_HOST"):
        _settings(alert_email_to=FOUNDER, alert_email_from="alerts@chatdeal.example")
    with pytest.raises(ValueError, match="ALERT_EMAIL_FROM"):
        _settings(alert_email_to=FOUNDER, smtp_host="smtp.example")
    with pytest.raises(ValueError, match="ALERT_EMAIL_TO"):
        _settings(alert_email_to="not-an-address", smtp_host="smtp.example", alert_email_from="alerts@chatdeal.example")
    with pytest.raises(ValueError, match="SMTP_PASSWORD"):
        _settings(alert_email_to=FOUNDER, smtp_host="smtp.example", alert_email_from="a@b.example", smtp_username="u")
    assert _settings().alert_emails == []                                        # unset: console only, as before


def test_the_old_severity_names_in_an_existing_env_file_still_load():
    assert _settings(alert_min_severity="warning").alert_min_severity == "medium"
    assert _settings(alert_min_severity="info").alert_min_severity == "low"
    assert _settings(alert_min_severity="critical").alert_min_severity == "critical"
    s = _settings()
    assert (s.alert_reminder_minutes, s.alert_high_cooldown_minutes, s.alert_medium_rollup_minutes, s.alert_low_digest_minutes) == (30, 30, 60, 1440)
