"""Connecting a WhatsApp number: the web component asks, the gateway talks to Meta (ADR 0025, rule B9).

Before this, the web API held the Meta app secret, called Meta's Graph API and encrypted the token itself. Now it writes a
connect request and waits for the gateway's answer. Everything the owner sees is unchanged (tests/test_embedded_signup.py
pins the responses); what is new is who does the work, and what happens when the gateway is not there. Meta's one-time
authorization code is held in the request only until it is used and never travels in an event or job."""
from __future__ import annotations

import asyncio
import uuid

from salesai.config import Settings
from tests.test_api import V1, hdr, owner
from tests.test_embedded_signup import CODE, body


async def _requests(world, shop):
    return await world.q(shop, "SELECT * FROM number_connect_requests ORDER BY created_at")


def _pn(shop) -> str:
    return f"pn-{shop.business_id.hex[:10]}"


async def test_the_gateway_does_the_work_and_the_request_records_the_outcome(world, client, signup_on):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(phone_number_id=_pn(shop)))
    assert r.status_code == 201
    [req] = await _requests(world, shop)
    assert (req["status"], req["code"]) == ("done", None)                          # the one-time code is cleared once used
    assert str(req["number_id"]) == r.json()["id"] and req["finished_at"] is not None


async def test_the_one_time_code_never_travels_in_an_event_or_job(world, client, signup_on):
    shop, tok = await owner(world, client)
    await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(phone_number_id=_pn(shop)))
    async with world.rt.db.system_tx() as c:
        outbox = await (await c.execute("SELECT payload::text AS p FROM outbox WHERE event_type='number.connect_requested'")).fetchall()
        jobs = await (await c.execute("SELECT payload::text AS p FROM jobs WHERE kind='connect_number'")).fetchall()
    assert outbox and jobs and not any(CODE in r["p"] for r in [*outbox, *jobs])


async def test_if_the_gateway_is_not_running_the_owner_is_told_and_nothing_connects_later(world, client, rt, signup_on, monkeypatch):
    shop, tok = await owner(world, client)
    monkeypatch.setattr(rt.settings, "number_connect_wait_s", 0.3)
    saved = rt.pump

    async def no_gateway() -> None:
        await asyncio.sleep(0.05)
    rt.pump = no_gateway
    try:
        r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(phone_number_id=_pn(shop)))
    finally:
        rt.pump = saved
    assert r.status_code == 502 and r.json()["error"]["code"] == "gateway_unavailable"
    assert signup_on.calls == []                                                   # the web side never reached Meta itself
    [req] = await _requests(world, shop)
    assert (req["status"], req["code"]) == ("failed", None)
    await world.drain(["outbound.actions"])                                          # the gateway comes back later...
    assert signup_on.calls == [] and await world.q(shop, "SELECT 1 FROM whatsapp_numbers WHERE channel='whatsapp_cloud'") == []


async def test_a_duplicate_number_is_a_conflict_and_the_request_says_why(world, client, signup_on):
    _, tok_a = await owner(world, client, "Shop A")
    shop_b, tok_b = await owner(world, client, "Shop B")
    pn = f"pn-dup-{uuid.uuid4().hex[:8]}"
    assert (await client.post(f"{V1}/numbers/connect", headers=hdr(tok_a), json=body(phone_number_id=pn))).status_code == 201
    r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok_b), json=body(phone_number_id=pn))
    assert r.status_code == 409
    [req] = await _requests(world, shop_b)
    assert (req["status"], req["error_status"], req["code"]) == ("failed", 409, None)


async def test_running_the_same_request_twice_connects_once(world, client, signup_on, rt):
    from salesai.events.outbox import emit
    shop, tok = await owner(world, client)
    assert (await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(phone_number_id=_pn(shop)))).status_code == 201
    [req] = await _requests(world, shop)
    async with world.rt.db.tenant(shop.business_id) as c:
        await emit(c, "number.connect_requested", {"request_id": req["id"]}, business_id=shop.business_id)   # at-least-once delivery
    calls_before = len(signup_on.calls)
    await world.drain(["outbound.actions"])
    assert len(signup_on.calls) == calls_before
    assert len(await world.q(shop, "SELECT 1 FROM whatsapp_numbers WHERE channel='whatsapp_cloud'")) == 1


async def test_a_request_the_owner_gave_up_on_is_skipped_and_its_code_cleared(world, client, signup_on, rt):
    from salesai.events.outbox import emit
    shop, _ = await owner(world, client)
    async with rt.db.tenant(shop.business_id) as c:
        row = await (await c.execute(
            """INSERT INTO number_connect_requests (business_id, code, waba_id, phone_number_id, coexistence, created_at)
               VALUES (%s,%s,'w','pn-old',false, now() - interval '30 minutes') RETURNING id""", (shop.business_id, CODE))).fetchone()
        await emit(c, "number.connect_requested", {"request_id": row["id"]}, business_id=shop.business_id)
    await world.drain(["outbound.actions"])
    [req] = await _requests(world, shop)
    assert (req["status"], req["code"]) == ("failed", None) and signup_on.calls == []


async def test_retention_clears_codes_the_gateway_never_got_to(world, client, signup_on, rt):
    from salesai.scheduler import Scheduler
    shop, _ = await owner(world, client)
    async with rt.db.tenant(shop.business_id) as c:
        await c.execute("INSERT INTO number_connect_requests (business_id, code, waba_id, phone_number_id, coexistence, created_at)"
                        " VALUES (%s,%s,'w','pn-stale',false, now() - interval '30 minutes')", (shop.business_id, CODE))
        await c.execute("INSERT INTO number_connect_requests (business_id, status, waba_id, phone_number_id, coexistence, created_at)"
                        " VALUES (%s,'done','w','pn-ancient',false, now() - interval '10 days')", (shop.business_id,))
    await Scheduler(rt).tick_retention()
    rows = await _requests(world, shop)
    assert [(r["status"], r["code"]) for r in rows] == [("failed", None)]


async def test_a_shop_never_sees_another_shops_connect_requests(world, client, signup_on):
    a, tok_a = await owner(world, client, "Shop A")
    b, tok_b = await owner(world, client, "Shop B")
    for tok in (tok_a, tok_b):
        await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(phone_number_id=f"pn-{uuid.uuid4().hex[:10]}"))
    for shop in (a, b):
        rows = await _requests(world, shop)
        assert len(rows) == 1 and rows[0]["business_id"] == shop.business_id


def test_the_wait_is_configuration_and_bounded():
    f = Settings.model_fields["number_connect_wait_s"]
    assert f.default == 30.0
