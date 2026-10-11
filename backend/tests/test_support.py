"""Support tickets: opened instantly from Settings, answered by platform staff, private to the shop."""
from __future__ import annotations

from tests.test_api import V1, hdr, operator_token, owner

TICKET = {"subject": "Messages are not arriving", "category": "problem", "message": "Since this morning customers say I do not reply."}


async def test_a_ticket_exists_the_moment_it_is_sent_and_support_is_told(world, client):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/support/tickets", headers=hdr(tok), json=TICKET)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["status"] == "open" and t["number"] > 0 and [m["author"] for m in t["messages"]] == ["shop"]
    listed = (await client.get(f"{V1}/support/tickets", headers=hdr(tok))).json()
    assert [x["id"] for x in listed] == [t["id"]] and listed[0]["last_message"].startswith("Since this morning")
    async with world.rt.db.system_tx() as c:
        n = (await (await c.execute("SELECT count(*) AS n FROM operator_alerts WHERE kind='support_ticket' AND business_id=%s", (shop.business_id,))).fetchone())["n"]
    assert n == 1


async def test_support_answers_in_the_same_thread_and_a_reply_from_the_shop_reopens_it(world, client):
    shop, tok = await owner(world, client)
    op = await operator_token(world, client)
    t = (await client.post(f"{V1}/support/tickets", headers=hdr(tok), json=TICKET)).json()
    queue = (await client.get(f"{V1}/operator/support", headers=hdr(op))).json()
    mine = next(x for x in queue if x["id"] == t["id"])
    assert mine["business_name"] == "Sharma Sarees" and mine["status"] == "open"

    a = await client.post(f"{V1}/operator/support/{t['id']}/messages", headers=hdr(op), json={"body": "Fixed on our side, please check."})
    assert a.status_code == 201 and a.json()["status"] == "answered"
    seen = (await client.get(f"{V1}/support/tickets/{t['id']}", headers=hdr(tok))).json()
    assert [m["author"] for m in seen["messages"]] == ["shop", "support"] and seen["status"] == "answered"

    done = (await client.post(f"{V1}/support/tickets/{t['id']}/resolve", headers=hdr(tok))).json()
    assert done["status"] == "resolved"
    again = (await client.post(f"{V1}/support/tickets/{t['id']}/messages", headers=hdr(tok), json={"body": "Still not working."})).json()
    assert again["status"] == "open" and len(again["messages"]) == 3
    closed = await client.put(f"{V1}/operator/support/{t['id']}/status", headers=hdr(op), json={"status": "resolved"})
    assert closed.status_code == 200 and closed.json()["status"] == "resolved"


async def test_tickets_are_private_to_their_shop_and_support_is_for_operators_only(world, client):
    _, a = await owner(world, client, "Shop A")
    _, b = await owner(world, client, "Shop B")
    t = (await client.post(f"{V1}/support/tickets", headers=hdr(a), json=TICKET)).json()
    assert (await client.get(f"{V1}/support/tickets", headers=hdr(b))).json() == []
    assert (await client.get(f"{V1}/support/tickets/{t['id']}", headers=hdr(b))).status_code == 404
    assert (await client.post(f"{V1}/support/tickets/{t['id']}/messages", headers=hdr(b), json={"body": "hi"})).status_code == 404
    assert (await client.post(f"{V1}/support/tickets/{t['id']}/resolve", headers=hdr(b))).status_code == 404
    assert (await client.get(f"{V1}/operator/support", headers=hdr(a))).status_code == 403
    assert (await client.post(f"{V1}/operator/support/{t['id']}/messages", headers=hdr(a), json={"body": "x"})).status_code == 403
    assert (await client.post(f"{V1}/support/tickets", json=TICKET)).status_code == 401


async def test_ticket_input_is_validated_and_capped(world, client):
    _, tok = await owner(world, client)
    assert (await client.post(f"{V1}/support/tickets", headers=hdr(tok), json={**TICKET, "subject": ""})).status_code == 422
    assert (await client.post(f"{V1}/support/tickets", headers=hdr(tok), json={**TICKET, "category": "gossip"})).status_code == 422
    assert (await client.post(f"{V1}/support/tickets", headers=hdr(tok), json={**TICKET, "extra": 1})).status_code == 422
    for _ in range(20):
        assert (await client.post(f"{V1}/support/tickets", headers=hdr(tok), json=TICKET)).status_code == 201
    r = await client.post(f"{V1}/support/tickets", headers=hdr(tok), json=TICKET)
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_tickets"


async def test_tickets_are_part_of_the_owner_data_export(world, client):
    _, tok = await owner(world, client)
    await client.post(f"{V1}/support/tickets", headers=hdr(tok), json=TICKET)
    data = (await client.get(f"{V1}/business/export", headers=hdr(tok))).json()
    assert len(data["support_tickets"]) == 1 and data["support_messages"][0]["body"].startswith("Since this morning")
