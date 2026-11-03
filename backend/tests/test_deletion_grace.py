"""A shop's deletion is scheduled, not immediate: the owner has a grace period (DELETION_GRACE_DAYS, 60 by default) to change their mind. Meanwhile the
assistant is paused and renewals stop; when the time comes the scheduler erases everything (the same erasure as before). The founder's console can still
erase at once for a legal request."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from salesai.modules.tenants.lifecycle import purge_due
from tests.test_api import V1, hdr, login, operator_token, owner


async def _request(client, tok, name):
    return await client.delete(f"{V1}/business", headers=hdr(tok), params={"confirm_name": name})


async def test_deleting_schedules_it_for_sixty_days_and_pauses_the_assistant(world, client):
    shop, tok = await owner(world, client, "Grace Shop")
    r = await _request(client, tok, "Grace Shop")
    assert r.status_code == 200 and r.json()["grace_days"] == 60 and "deleted" not in r.json()
    due = datetime.fromisoformat(r.json()["scheduled_for"])
    assert timedelta(days=59, hours=23) < due - datetime.now(UTC) < timedelta(days=60, hours=1)
    b = (await client.get(f"{V1}/business", headers=hdr(tok))).json()          # still there, still the owner's, and says so
    assert b["ai_enabled"] is False and b["deletion_scheduled_for"] is not None
    assert (await client.get(f"{V1}/business/export", headers=hdr(tok))).status_code == 200      # they can still take their data
    assert await world.q(shop, "SELECT 1 FROM products") is not None


async def test_asking_twice_does_not_move_the_date_and_a_wrong_name_schedules_nothing(world, client):
    shop, tok = await owner(world, client, "Twice Shop")
    assert (await _request(client, tok, "wrong")).status_code == 422
    assert (await client.get(f"{V1}/business", headers=hdr(tok))).json()["deletion_scheduled_for"] is None
    first = (await _request(client, tok, "Twice Shop")).json()["scheduled_for"]
    assert (await _request(client, tok, "Twice Shop")).json()["scheduled_for"] == first


async def test_cancelling_restores_the_assistant_and_leaves_no_trace_of_the_request(world, client):
    shop, tok = await owner(world, client, "Cancel Shop")
    assert (await client.get(f"{V1}/business", headers=hdr(tok))).json()["ai_enabled"] is True
    await _request(client, tok, "Cancel Shop")
    r = await client.post(f"{V1}/business/deletion/cancel", headers=hdr(tok))
    assert r.status_code == 200
    b = (await client.get(f"{V1}/business", headers=hdr(tok))).json()
    assert b["ai_enabled"] is True and b["deletion_scheduled_for"] is None
    assert (await client.post(f"{V1}/business/deletion/cancel", headers=hdr(tok))).status_code == 404      # nothing left to cancel


async def test_it_stays_paused_if_it_was_paused_before_and_only_the_owner_can_cancel(world, client):
    shop, tok = await owner(world, client, "Paused Shop")
    op = await operator_token(world, client)
    await client.patch(f"{V1}/operator/businesses/{shop.business_id}", headers=hdr(op), json={"ai_enabled": False})
    await _request(client, tok, "Paused Shop")
    await client.post(f"{V1}/team", headers=hdr(tok), json={"phone": "+919811100077", "name": "Staffer", "role": "staff"})
    st = await login(client, "+919811100077")
    assert (await client.post(f"{V1}/business/deletion/cancel", headers=hdr(st))).status_code == 403
    await client.post(f"{V1}/business/deletion/cancel", headers=hdr(tok))
    assert (await client.get(f"{V1}/business", headers=hdr(tok))).json()["ai_enabled"] is False           # restored to what it was, not forced on


async def test_the_scheduler_erases_it_only_once_the_grace_has_passed_and_leaves_a_record(world, client, rt):
    shop, tok = await owner(world, client, "Purge Shop")
    due = datetime.fromisoformat((await _request(client, tok, "Purge Shop")).json()["scheduled_for"])
    assert await purge_due(rt.db, rt.queue, now=due - timedelta(minutes=1)) == 0
    assert (await client.get(f"{V1}/business", headers=hdr(tok))).status_code == 200
    assert await purge_due(rt.db, rt.queue, now=due + timedelta(minutes=1)) >= 1
    assert (await client.get(f"{V1}/business", headers=hdr(tok))).status_code == 401              # gone: the session has no shop left
    async with rt.db.system_tx() as c:
        a = await (await c.execute("SELECT actor_label, detail FROM platform_audit_log WHERE business_id=%s AND action='tenant.purged'", (shop.business_id,))).fetchall()
        assert len(a) == 1 and "name" not in str(a[0]["detail"]).lower()                           # the record names ids and counts, not people


async def test_the_scheduler_has_a_duty_for_it(world):
    from salesai.scheduler import DUTIES
    assert "deletions" in DUTIES["web"] and "deletions" in DUTIES["all"] and "deletions" not in DUTIES["agent"]
