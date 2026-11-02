"""Auditing and retention (ADR 0030).

Audit history cannot be rewritten by any application role. Platform actions on a shop (export, deletion,
impersonation, operator changes) leave a trail that outlives the shop: a shop's own audit log is erased with the shop
under DPDP deletion, which used to erase the record of the deletion itself. Consent changes (opt-out, personal
contact) are audited like pricing and settings.

Retention is declared configuration. Clearly temporary data is purged on a schedule; chat history is kept while the
shop is active unless a retention period is set (a privacy/legal policy for the founder, on the go-live checklist).
When it is set, deletion runs shop by shop through the shop's own row-secured role, so the platform role never needs
delete rights over chats - and it is tested on a database with data in two shops, the case that hid rows before."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from salesai.config import Settings
from salesai.scheduler import Scheduler
from tests.test_api import V1, hdr, login, operator_token
from tests.test_conversation_flow import shop_with_catalog

OLD, NEW = "now() - interval '400 days'", "now() - interval '1 day'"


async def _x(world, shop, sql: str, *args) -> None:
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute(sql, args or None)


async def _platform_audit(world, business_id) -> list[dict]:
    async with world.rt.db.system_tx() as c:
        return await (await c.execute(
            "SELECT action, actor_label, detail FROM platform_audit_log WHERE business_id=%s ORDER BY id", (business_id,))).fetchall()


# ---------------------------------------------------------------- audit history cannot be rewritten

async def test_no_application_role_can_edit_or_delete_audit_history(world):
    shop, _ = await shop_with_catalog(world)
    await _x(world, shop, "INSERT INTO audit_log (business_id, actor_label, action, entity) VALUES (%s,'owner','x','business')", shop.business_id)
    for stmt in ("UPDATE audit_log SET action='forged'", "DELETE FROM audit_log"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await _x(world, shop, stmt)                                       # the shop's role
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with world.rt.db.system_tx() as c:                          # the platform's role
                await c.execute(stmt)
    for stmt in ("UPDATE platform_audit_log SET action='forged'", "DELETE FROM platform_audit_log"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with world.rt.db.system_tx() as c:
                await c.execute(stmt)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await _x(world, shop, "SELECT 1 FROM platform_audit_log")             # shops never see the platform trail


async def test_export_and_deletion_leave_a_trail_that_outlives_the_shop(world, client):
    shop, _ = await shop_with_catalog(world)
    ops = await operator_token(world, client)
    assert (await client.get(f"{V1}/operator/businesses/{shop.business_id}/export", headers=hdr(ops))).status_code == 200
    r = await client.delete(f"{V1}/operator/businesses/{shop.business_id}", headers=hdr(ops), params={"confirm_name": "Sharma Sarees"})
    assert r.status_code == 200
    actions = [a["action"] for a in await _platform_audit(world, shop.business_id)]
    assert actions == ["tenant.exported", "tenant.deleted"]                    # the shop is gone; its deletion is not


async def test_platform_actions_on_a_shop_are_recorded_on_the_platform_trail(world, client):
    shop, _ = await shop_with_catalog(world)
    ops = await operator_token(world, client)
    await client.patch(f"{V1}/operator/businesses/{shop.business_id}", headers=hdr(ops), json={"ai_enabled": False})
    await client.post(f"{V1}/operator/businesses/{shop.business_id}/impersonate", headers=hdr(ops))
    actions = [a["action"] for a in await _platform_audit(world, shop.business_id)]
    assert "operator.updated" in actions and "operator.impersonation" in actions


async def test_consent_changes_are_audited(world, client):
    shop, _ = await shop_with_catalog(world)
    owner = await login(client, shop.owner_phone)
    await world.customer_says(shop, "+919600000301", "hi")
    await world.drain(["inbound.events"])
    [cust] = await world.q(shop, "SELECT id FROM customers WHERE wa_id='919600000301'")
    await client.patch(f"{V1}/customers/{cust['id']}", headers=hdr(owner), json={"opted_out": True})
    await client.post(f"{V1}/contacts/personal", headers=hdr(owner), json={"phone": "+919600000302", "name": "Mum"})
    actions = {r["action"] for r in await world.q(shop, "SELECT action FROM audit_log")}
    assert {"customer.opt_out_changed", "customer.marked_personal"} <= actions


# ---------------------------------------------------------------- retention

def test_retention_is_configuration_and_chat_history_is_kept_unless_a_period_is_set():
    f = Settings.model_fields
    assert f["chat_retention_days"].default == 0 and f["turn_retention_days"].default == 0       # 0 = keep while the shop is active
    assert f["sim_message_retention_days"].default == 30
    assert f["resolved_alert_retention_days"].default == 180
    assert f["resolved_ticket_attachment_retention_days"].default == 180


async def test_temporary_data_is_purged_on_schedule(world):
    shop, _ = await shop_with_catalog(world)
    async with world.rt.db.system_tx() as c:
        await c.execute(f"INSERT INTO sim_messages (phone, business_phone, direction, kind, body, created_at) VALUES ('91999','x','to_user','text','old', {OLD})")  # noqa: S608
        await c.execute(f"INSERT INTO sim_messages (phone, business_phone, direction, kind, body, created_at) VALUES ('91999','x','to_user','text','new', {NEW})")  # noqa: S608
        for state in ("resolved", "open"):
            await c.execute(f"""INSERT INTO operator_alerts (business_id, severity, kind, message, created_at, resolved_at)
                                VALUES (%s,'warning','retention-test','{state}', {OLD}, {OLD if state == 'resolved' else 'NULL'})""", (shop.business_id,))  # noqa: S608
    await Scheduler(world.rt).tick_retention()
    async with world.rt.db.system_tx() as c:
        sims = {r["body"] for r in await (await c.execute("SELECT body FROM sim_messages WHERE phone='91999'")).fetchall()}
        alerts = {r["message"] for r in await (await c.execute("SELECT message FROM operator_alerts WHERE kind='retention-test'")).fetchall()}
    assert sims == {"new"}
    assert alerts == {"open"}                                                  # an unresolved alert is never purged, however old


async def test_chat_retention_once_set_removes_only_old_rows_in_every_shop(world, monkeypatch):
    shops = [(await shop_with_catalog(world))[0] for _ in range(2)]
    for shop in shops:
        await world.customer_says(shop, "+919600000303", "hello")
        await world.drain(["inbound.events"])
        [conv] = await world.q(shop, "SELECT id FROM conversations")
        for when, body in ((OLD, "old"), (NEW, "new")):
            await _x(world, shop, f"INSERT INTO messages (business_id, conversation_id, direction, sender, body, created_at) VALUES (%s,%s,'in','customer',%s,{when})",  # noqa: S608
                     shop.business_id, conv["id"], body)
            await _x(world, shop, f"INSERT INTO owner_messages (business_id, business_user_id, direction, body, created_at) VALUES (%s,%s,'out',%s,{when})",  # noqa: S608
                     shop.business_id, shop.business_user_id, body)
    monkeypatch.setattr(world.rt.settings, "chat_retention_days", 365)
    await Scheduler(world.rt).tick_retention()
    for shop in shops:                                                          # both shops: deletion is per shop, under RLS
        assert "old" not in {r["body"] for r in await world.q(shop, "SELECT body FROM messages")}
        assert "new" in {r["body"] for r in await world.q(shop, "SELECT body FROM messages")}
        assert {r["body"] for r in await world.q(shop, "SELECT body FROM owner_messages")} == {"new"}


async def test_attachments_of_long_resolved_tickets_are_purged_and_open_ones_kept(world):
    shop, _ = await shop_with_catalog(world)
    for status in ("resolved", "open"):
        tk = uuid.uuid4()
        await _x(world, shop, f"INSERT INTO support_tickets (id, business_id, subject, category, status, updated_at) VALUES (%s,%s,%s,'question',%s,{OLD})",  # noqa: S608
                 tk, shop.business_id, status, status)
        await _x(world, shop, "INSERT INTO support_messages (business_id, ticket_id, author, body) VALUES (%s,%s,'shop','pic')", shop.business_id, tk)
        [m] = await world.q(shop, "SELECT id FROM support_messages WHERE ticket_id=%s", tk)
        await _x(world, shop, f"INSERT INTO support_attachments (business_id, ticket_id, message_id, content_type, size_bytes, data, created_at) VALUES (%s,%s,%s,'image/png',1,'\\\\x00'::bytea,{OLD})",  # noqa: S608
                 shop.business_id, tk, m["id"])
    await Scheduler(world.rt).tick_retention()
    rows = await world.q(shop, "SELECT t.status FROM support_attachments a JOIN support_tickets t ON t.id=a.ticket_id")
    assert [r["status"] for r in rows] == ["open"]
