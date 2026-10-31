"""Channel gateway, raw webhook log (ADR 0020 follow-up 1): the exact request is kept, bad-signature requests are kept as
flagged evidence but never processed, and retention is a setting."""
from __future__ import annotations

import json

from salesai.modules.channels.ingress import accept_webhook
from salesai.modules.channels.simulator import build_message_payload
from salesai.modules.channels.whatsapp import sign
from salesai.scheduler import Scheduler


def _raw(shop, text="hello"):
    payload = build_message_payload(shop.phone_number_id, shop.business_phone, "+919800001001", text)
    return json.dumps(payload, separators=(",", ":")).encode()


async def _rows(world, where="true"):
    async with world.rt.db.system_tx() as c:
        return await (await c.execute(f"SELECT * FROM webhook_events WHERE {where} ORDER BY id")).fetchall()  # noqa: S608


async def test_valid_webhook_keeps_exact_bytes_signature_and_time(world):
    shop = await world.make_shop()
    raw = _raw(shop)
    sig = sign(world.s.meta_app_secret, raw)
    code, _ = await accept_webhook(world.rt.db, world.s.meta_app_secret, raw, sig)
    assert code == 200
    [row] = [r for r in await _rows(world, "signature_valid") if bytes(r["raw_body"]) == raw]
    assert row["signature"] == sig and row["received_at"] is not None
    assert row["payload"]["object"] == "whatsapp_business_account"


async def test_bad_signature_is_kept_flagged_and_never_queued(world):
    shop = await world.make_shop()
    raw = _raw(shop)
    for sig in (None, "sha256=deadbeef", sign("wrong-secret", raw)):
        code, _ = await accept_webhook(world.rt.db, world.s.meta_app_secret, raw, sig)
        assert code == 401
    bad = await _rows(world, "NOT signature_valid")
    assert len(bad) == 3
    assert {r["signature"] for r in bad} == {None, "sha256=deadbeef", sign("wrong-secret", raw)}
    assert all(bytes(r["raw_body"]) == raw and r["payload"] is None for r in bad)
    async with world.rt.db.system_tx() as c:
        queued = await (await c.execute("SELECT count(*) AS n FROM outbox o JOIN webhook_events w ON (o.payload->>'webhook_event_id')::bigint = w.id "
                                        "WHERE o.event_type='webhook.received' AND NOT w.signature_valid")).fetchone()
    assert queued["n"] == 0


async def test_bad_signature_body_is_size_capped_and_oversize_is_not_stored(world):
    cap = world.s.invalid_webhook_max_bytes
    big = b"x" * (cap * 3)
    code, _ = await accept_webhook(world.rt.db, world.s.meta_app_secret, big, "sha256=00")
    assert code == 401
    [row] = await _rows(world, f"NOT signature_valid AND length(raw_body) = {cap}")
    assert bytes(row["raw_body"]) == big[:cap]
    before = len(await _rows(world))
    code, _ = await accept_webhook(world.rt.db, world.s.meta_app_secret, b"y" * 1_000_001, "sha256=00")
    assert code == 413 and len(await _rows(world)) == before


async def test_signed_but_unreadable_body_is_kept_and_not_queued(world):
    raw = b"not json at all"
    code, _ = await accept_webhook(world.rt.db, world.s.meta_app_secret, raw, sign(world.s.meta_app_secret, raw))
    assert code == 400
    [row] = [r for r in await _rows(world) if bytes(r["raw_body"] or b"") == raw]
    assert row["signature_valid"] and row["payload"] is None and row["error"]


async def test_an_invalid_stored_event_is_never_routed(world):
    shop = await world.make_shop()
    raw = _raw(shop, "sneaky")
    await accept_webhook(world.rt.db, world.s.meta_app_secret, raw, "sha256=bad")
    [row] = [r for r in await _rows(world, "NOT signature_valid") if bytes(r["raw_body"]) == raw]
    from salesai.queue.base import Job, JobSpec
    await world.rt.router.route_webhook(Job("0", JobSpec("inbound.events", "route_webhook", {"webhook_event_id": row["id"]}), 1))
    assert await world.q(shop, "SELECT 1 FROM messages") == []


async def test_retention_is_a_setting_and_invalid_requests_expire_sooner(world):
    s = world.rt.settings
    async with world.rt.db.system_tx() as c:
        for valid, age in ((True, s.raw_webhook_retention_days + 1), (True, 1), (False, s.invalid_webhook_retention_days + 1), (False, 0)):
            await c.execute("INSERT INTO webhook_events (signature_valid, payload, raw_body, received_at) VALUES (%s, %s, %s, now() - make_interval(days => %s))",
                            (valid, '{"t":1}' if valid else None, f"{valid}-{age}".encode(), age))
    await Scheduler(world.rt).tick_retention()
    left = {bytes(r["raw_body"]).decode() for r in await _rows(world, "raw_body IS NOT NULL")}
    assert f"True-{s.raw_webhook_retention_days + 1}" not in left and "True-1" in left
    assert f"False-{s.invalid_webhook_retention_days + 1}" not in left and "False-0" in left
