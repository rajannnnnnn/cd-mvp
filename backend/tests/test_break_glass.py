"""ADR 0028: platform staff do not browse customer conversations. Reading one is break-glass: a stated reason, a time limit,
the append-only platform trail, and a line in the shop's own view of who looked and why."""
from __future__ import annotations

import json
import uuid

from tests.test_analytics import seed_activity
from tests.test_api import V1, hdr, operator_token, owner

OP = f"{V1}/operator"


async def opened(client, op, shop, reason="Customer reported a wrong price; checking the chat", minutes=15):
    r = await client.post(f"{OP}/businesses/{shop.business_id}/break-glass", headers=hdr(op), json={"reason": reason, "minutes": minutes})
    assert r.status_code == 201, r.text
    return r.json()


async def platform_rows(world, shop, action):
    async with world.rt.db.system_tx() as c:
        return await (await c.execute("SELECT * FROM platform_audit_log WHERE business_id=%s AND action=%s ORDER BY id", (shop.business_id, action))).fetchall()


async def test_reading_a_conversation_needs_a_grant(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Closed Shop")
    convs = await seed_activity(world, shop, days_ago=1, customers=1, deals=0)
    for path in (f"{OP}/businesses/{shop.business_id}/conversations", f"{OP}/businesses/{shop.business_id}/conversations/{convs[0]}/messages"):
        r = await client.get(path, headers=hdr(op))
        assert r.status_code == 403 and r.json()["error"]["code"] == "break_glass_required"
    r = await client.get(f"{OP}/businesses/{shop.business_id}/conversations", headers=hdr(op), params={"grant": str(uuid.uuid4())})
    assert r.status_code == 403


async def test_a_grant_needs_a_real_reason_and_a_short_time(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Reason Shop")
    url = f"{OP}/businesses/{shop.business_id}/break-glass"
    for body in ({"reason": "", "minutes": 15}, {"reason": "short", "minutes": 15}, {"reason": "x" * 10, "minutes": 0},
                 {"reason": "A proper reason for looking", "minutes": 61}):
        assert (await client.post(url, headers=hdr(op), json=body)).status_code == 422
    assert (await client.post(f"{OP}/businesses/{uuid.uuid4()}/break-glass", headers=hdr(op), json={"reason": "A proper reason for looking", "minutes": 5})).status_code == 404
    g = await opened(client, op, shop, minutes=5)
    assert g["id"] and g["expires_at"]


async def test_with_a_grant_the_conversation_can_be_read_and_every_read_is_on_the_trail(world, client):
    op = await operator_token(world, client)
    shop, tok = await owner(world, client, "Open Shop")
    convs = await seed_activity(world, shop, days_ago=1, customers=2, deals=0)
    g = await opened(client, op, shop)
    listing = await client.get(f"{OP}/businesses/{shop.business_id}/conversations", headers=hdr(op), params={"grant": g["id"]})
    assert listing.status_code == 200 and len(listing.json()) == 2
    r = await client.get(f"{OP}/businesses/{shop.business_id}/conversations/{convs[0]}/messages", headers=hdr(op), params={"grant": g["id"]})
    assert r.status_code == 200 and [m["body"] for m in r.json()] == ["hi", "hello"]
    opened_rows = await platform_rows(world, shop, "break_glass.opened")
    read_rows = await platform_rows(world, shop, "break_glass.read")
    assert opened_rows and read_rows
    d = read_rows[-1]["detail"]
    assert d["conversation_id"] == str(convs[0]) and d["messages"] == 2 and "hello" not in json.dumps(d)         # the trail names what was read, never the words
    assert "Customer reported a wrong price" in json.dumps(opened_rows[-1]["detail"])


async def test_the_shop_can_see_who_from_the_platform_looked_and_why(world, client):
    op = await operator_token(world, client)
    shop, tok = await owner(world, client, "Watching Shop")
    other, other_tok = await owner(world, client, "Other Watching Shop")
    convs = await seed_activity(world, shop, days_ago=1, customers=1, deals=0)
    g = await opened(client, op, shop, reason="Investigating the failed delivery you reported")
    await client.get(f"{OP}/businesses/{shop.business_id}/conversations/{convs[0]}/messages", headers=hdr(op), params={"grant": g["id"]})
    r = await client.get(f"{V1}/business/platform-access", headers=hdr(tok))
    assert r.status_code == 200
    rows = r.json()
    assert {x["action"] for x in rows} >= {"platform.access_opened", "platform.conversation_viewed"}
    assert any("Investigating the failed delivery you reported" in (x["reason"] or "") for x in rows)
    assert (await client.get(f"{V1}/business/platform-access", headers=hdr(other_tok))).json() == []              # another shop sees none of it


async def test_only_the_owner_sees_the_platform_access_list(world, client):
    shop, tok = await owner(world, client, "Staff View Shop")
    assert (await client.get(f"{V1}/business/platform-access")).status_code == 401


async def test_a_grant_runs_out_ends_early_and_covers_one_shop(world, client):
    op = await operator_token(world, client)      # there is one founder (ADR 0047): enrolling again ends every session, so no second operator exists to refuse
    shop, other = await world.make_shop("Grant Shop"), await world.make_shop("Not Granted Shop")
    convs = await seed_activity(world, shop, days_ago=1, customers=1, deals=0)
    other_convs = await seed_activity(world, other, days_ago=1, customers=1, deals=0)
    g = await opened(client, op, shop)
    msgs = f"{OP}/businesses/{shop.business_id}/conversations/{convs[0]}/messages"
    assert (await client.get(f"{OP}/businesses/{other.business_id}/conversations/{other_convs[0]}/messages", headers=hdr(op), params={"grant": g["id"]})).status_code == 403
    assert (await client.get(f"{OP}/businesses/{shop.business_id}/conversations/{other_convs[0]}/messages", headers=hdr(op), params={"grant": g["id"]})).status_code == 404
    assert (await client.get(msgs, headers=hdr(op), params={"grant": g["id"]})).status_code == 200
    assert (await client.delete(f"{OP}/break-glass/{g['id']}", headers=hdr(op))).status_code == 204              # ended early
    assert (await client.get(msgs, headers=hdr(op), params={"grant": g["id"]})).status_code == 403
    g2 = await opened(client, op, shop)
    async with world.rt.db.console_tx() as c:
        await c.execute("UPDATE break_glass_grants SET expires_at = now() - interval '1 second' WHERE id=%s", (g2["id"],))
    assert (await client.get(msgs, headers=hdr(op), params={"grant": g2["id"]})).status_code == 403              # ran out
    assert await platform_rows(world, shop, "break_glass.ended")


async def test_signing_in_as_a_shop_needs_a_reason_and_is_shown_to_the_shop(world, client):
    op = await operator_token(world, client)
    shop, tok = await owner(world, client, "Impersonated Shop")
    url = f"{OP}/businesses/{shop.business_id}/impersonate"
    assert (await client.post(url, headers=hdr(op))).status_code == 422
    assert (await client.post(url, headers=hdr(op), json={"reason": "short"})).status_code == 422
    r = await client.post(url, headers=hdr(op), json={"reason": "Setting up the pricing together on a call"})
    assert r.status_code == 200
    rows = (await client.get(f"{V1}/business/platform-access", headers=hdr(tok))).json()
    assert any(x["action"] == "platform.impersonated" and "Setting up the pricing" in (x["reason"] or "") for x in rows)
    assert any("Setting up the pricing" in json.dumps(x["detail"]) for x in await platform_rows(world, shop, "operator.impersonation"))


async def test_the_grants_table_is_out_of_reach_of_the_shop_role(world):
    shop = await world.make_shop("Grants Hidden Shop")
    import psycopg
    async with world.rt.db.tenant(shop.business_id) as c:
        try:
            await c.execute("SELECT 1 FROM break_glass_grants")
            raise AssertionError("the shop role could read break_glass_grants")
        except psycopg.errors.InsufficientPrivilege:
            pass
