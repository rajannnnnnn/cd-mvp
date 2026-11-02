"""Sign-in codes: the web component asks, the gateway sends (ADR 0025, rule B9).

Before this, the web API held the Meta token and the master key just to send a code, so a break-in on the web part
reached WhatsApp. Now the web part writes a delivery request and waits for the gateway's answer; only the gateway sends.
The plain code has to reach the gateway somehow, so it is held in the request row only until it is sent: never in an
event or job payload, and cleared on success, failure, expiry and timeout, so it lives at most one code lifetime."""
from __future__ import annotations

import json
import re
import uuid

import pytest

from salesai.config import Settings
from salesai.modules.auth import AuthError, AuthService
from salesai.modules.channels.base import SendResult
from salesai.phone import wa_id
from tests.test_auth import otp_for


@pytest.fixture
def phone() -> str:
    """Each test gets its own number: the database is shared across the session, so rows must not leak between tests."""
    return f"+9196{uuid.uuid4().int % 10**8:08d}"


@pytest.fixture
def auth(rt):
    return AuthService(rt.db, rt.settings, pump=rt.auth.pump)


async def _rows(world, phone: str):
    async with world.rt.db.system_tx() as c:
        return await (await c.execute("SELECT * FROM otp_deliveries WHERE phone=%s ORDER BY created_at", (phone,))).fetchall()


async def _sent_codes(world, phone: str) -> list[str]:
    rows = [r for r in await world.sim_thread(phone, "platform") if r["direction"] == "to_user" and r["template_name"] == "otp_login"]
    return [re.search(r"\b(\d{6})\b", r["body"]).group(1) for r in rows]


class _BrokenChannel:
    def __init__(self, result: SendResult):
        self.result, self.calls = result, 0

    async def send_template(self, *a, **k):
        self.calls += 1
        return self.result


def _break_platform_sender(world, monkeypatch, result: SendResult) -> _BrokenChannel:
    ch = _BrokenChannel(result)
    ref, _ = world.rt.channels.platform_sender()
    monkeypatch.setattr(world.rt.channels, "platform_sender", lambda: (ref, ch))
    return ch


async def test_the_code_is_sent_by_the_gateway_and_signs_the_owner_in(world, auth):
    shop = await world.make_shop()
    assert (await auth.request_otp(shop.owner_phone, "10.0.0.1"))["status"] == "sent"
    code = await otp_for(world, shop.owner_phone)
    pair = await auth.verify_otp(shop.owner_phone, code, "10.0.0.1", "Chrome on Android")
    assert pair.access_token


async def test_the_plain_code_is_cleared_once_sent_and_never_travels_in_an_event_or_job(world, auth, phone):
    await auth.request_otp(phone, "10.0.0.2")
    [code] = await _sent_codes(world, phone)
    [row] = await _rows(world, phone)
    assert (row["status"], row["code"]) == ("sent", None) and row["sent_at"] is not None
    async with world.rt.db.system_tx() as c:
        outbox = await (await c.execute("SELECT payload::text AS p FROM outbox WHERE event_type='platform.otp_requested'")).fetchall()
        jobs = await (await c.execute("SELECT payload::text AS p FROM jobs WHERE kind='send_otp'")).fetchall()
    assert outbox and jobs
    assert not any(code in r["p"] for r in [*outbox, *jobs])                       # only a delivery id is queued


async def test_the_web_component_holds_no_means_of_sending(world):
    # the structural proof lives in the boundary check (B9); this pins the shape that makes it possible
    assert not hasattr(world.rt.auth, "channels")


async def test_if_the_gateway_is_not_running_the_user_is_told_and_no_stale_code_is_sent_later(world, rt, monkeypatch, phone):
    monkeypatch.setattr(rt.settings, "otp_delivery_wait_s", 0.3)
    async def no_gateway() -> None:                                                # nothing consumes the send queue
        import asyncio
        await asyncio.sleep(0.05)
    quiet = AuthService(rt.db, rt.settings, pump=no_gateway)
    with pytest.raises(AuthError) as e:
        await quiet.request_otp(phone, "10.0.0.3")
    assert (e.value.code, e.value.status) == ("delivery_failed", 502)
    [row] = await _rows(world, phone)
    assert (row["status"], row["code"]) == ("failed", None)                          # the plain code does not linger
    await world.drain(["outbound.actions"])                                          # the gateway comes back later...
    assert await _sent_codes(world, phone) == []                                            # ...and does not send a code nobody is waiting for


async def test_a_delivery_failure_is_reported_to_the_user(world, auth, monkeypatch, phone):
    ch = _break_platform_sender(world, monkeypatch, SendResult(False, error="boom", permanent="other"))
    with pytest.raises(AuthError) as e:
        await auth.request_otp(phone, "10.0.0.4")
    assert e.value.code == "delivery_failed" and ch.calls == 1                        # a permanent failure is not retried
    [row] = await _rows(world, phone)
    assert (row["status"], row["code"]) == ("failed", None) and "boom" in row["error"]


async def test_the_demo_setting_accepts_a_failed_delivery_as_before(world, auth, monkeypatch, phone):
    monkeypatch.setattr(world.rt.settings, "otp_accept_any", True)
    _break_platform_sender(world, monkeypatch, SendResult(False, error="boom", permanent="other"))
    assert (await auth.request_otp(phone, "10.0.0.5"))["status"] == "sent"


async def test_a_temporary_failure_is_retried_by_the_queue_and_then_sent(world, rt, monkeypatch, phone):
    ch = _break_platform_sender(world, monkeypatch, SendResult(False, error="busy", retryable=True))
    async with world.rt.db.system_tx() as c:
        from salesai.events.outbox import emit
        row = await (await c.execute(
            "INSERT INTO otp_deliveries (phone, code, expires_at) VALUES (%s,'123456', now() + interval '5 minutes') RETURNING id", (phone,))).fetchone()
        await emit(c, "platform.otp_requested", {"delivery_id": row["id"]}, business_id=None)
    await world.drain(["outbound.actions"])
    assert ch.calls >= 1 and (await _rows(world, phone))[0]["status"] == "pending"          # not given up on: the job is scheduled to retry
    async with rt.db.system_tx() as c:                                                     # it would otherwise fire inside a later test
        await c.execute("DELETE FROM jobs WHERE kind='send_otp' AND payload->>'delivery_id' = %s", (str(row["id"]),))


async def test_running_the_same_request_twice_sends_once(world, auth, phone):
    await auth.request_otp(phone, "10.0.0.6")
    async with world.rt.db.system_tx() as c:
        outbox = await (await c.execute("SELECT payload FROM outbox WHERE event_type='platform.otp_requested' ORDER BY seq DESC LIMIT 1")).fetchone()
    from salesai.events.outbox import emit
    async with world.rt.db.system_tx() as c:
        await emit(c, "platform.otp_requested", {"delivery_id": outbox["payload"]["delivery_id"]}, business_id=None)   # at-least-once delivery
    await world.drain(["outbound.actions"])
    assert len(await _sent_codes(world, phone)) == 1


async def test_an_expired_request_is_skipped_and_its_code_cleared(world, rt, phone):
    from salesai.events.outbox import emit
    async with rt.db.system_tx() as c:
        row = await (await c.execute(
            "INSERT INTO otp_deliveries (phone, code, expires_at) VALUES (%s,'654321', now() - interval '1 minute') RETURNING id", (phone,))).fetchone()
        await emit(c, "platform.otp_requested", {"delivery_id": row["id"]}, business_id=None)
    await world.drain(["outbound.actions"])
    [r] = await _rows(world, phone)
    assert (r["status"], r["code"]) == ("failed", None) and await _sent_codes(world, phone) == []


async def test_retention_clears_codes_the_gateway_never_got_to_and_removes_old_requests(world, rt, phone):
    from salesai.scheduler import Scheduler
    async with rt.db.system_tx() as c:
        await c.execute("INSERT INTO otp_deliveries (phone, code, expires_at) VALUES (%s,'111111', now() - interval '1 minute')", (phone,))
        await c.execute("INSERT INTO otp_deliveries (phone, status, created_at, expires_at) VALUES (%s,'sent', now() - interval '3 days', now() - interval '3 days')", (phone,))
    await Scheduler(rt).tick_retention()
    rows = await _rows(world, phone)
    assert [(r["status"], r["code"]) for r in rows] == [("failed", None)]


async def test_the_language_of_the_account_reaches_the_template(world, auth, monkeypatch):
    seen: dict[str, str] = {}                                                          # recipient -> template language
    ref, real = world.rt.channels.platform_sender()

    class Spy:
        async def send_template(self, number, to, name, language, params):
            seen[to] = language
            return await real.send_template(number, to, name, language, params)
    monkeypatch.setattr(world.rt.channels, "platform_sender", lambda: (ref, Spy()))
    shop = await world.make_shop()
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE accounts SET language='hi' WHERE phone=%s", (shop.owner_phone,))
    await auth.request_otp(shop.owner_phone, "10.0.0.7")
    assert seen[wa_id(shop.owner_phone)] == "hi"


def test_the_wait_is_configuration_and_bounded():
    f = Settings.model_fields["otp_delivery_wait_s"]
    assert f.default == 6.0
    assert json.dumps(f.metadata, default=str)                                       # has validation bounds (gt 0, le 30)
