"""Meta message usage per shop (ADR 0026): what Meta will bill, estimated from what we actually delivered.

Meta bills per delivered message; since 1 Oct 2026 each business number gets 1,000 free service messages a calendar
month (rates and allowance are configuration and must be re-checked against Meta's own pricing). Two things decide
the estimate and are easy to get wrong, so they are pinned here:
- only messages WE sent through the API are billed. An owner replying from their own WhatsApp Business app
  (coexistence) comes back to us as an echo, but Meta does not bill it;
- who pays depends on whose WhatsApp account the number is on: the shop's own (Embedded Signup, its own token) or
  the platform's (pilot numbers registered under our account, FR-ON-3).
Estimates only: Meta's invoice is the source of truth."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from salesai.config import Settings
from salesai.modules.billing import MetaUsage
from tests.test_api import V1, hdr, login, operator_token
from tests.test_secrets import settings as make_settings

NOV = datetime(2026, 11, 10, tzinfo=UTC)
OCT = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
RATES = {"service": 0.115, "utility": 0.115, "authentication": 0.115, "marketing": 0.8631}


async def _x(world, shop, sql: str, *args) -> None:
    """Write as the tenant (world.q is for reads: it fetches rows)."""
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute(sql, args or None)


async def _conversation(world, shop) -> str:
    await world.customer_says(shop, "+919800000001", "hello")
    await world.drain(["inbound.events"])
    return str((await world.conv(shop, "+919800000001"))["id"])


async def _out(world, shop, conv: str, n: int, *, delivered_at=NOV, sender="ai", via="api", status="delivered") -> None:
    for _ in range(n):
        await _x(world, shop, """INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status, delivered_at, sent_via)
                               VALUES (%s,%s,'out',%s,'text','x',%s,%s,%s)""", shop.business_id, conv, sender, status, delivered_at, via)


async def _owner_out(world, shop, *, kind="text", number=True, status="delivered", n=1) -> None:
    for _ in range(n):
        await _x(world, shop, """INSERT INTO owner_messages (business_id, business_user_id, direction, kind, purpose, body, status, sent_at, whatsapp_number_id)
                               VALUES (%s,%s,'out',%s,'handoff_alert','x',%s,%s,%s)""",
                      shop.business_id, shop.business_user_id, kind, status, NOV, shop.number_id if number else None)


def usage(world, *, free: int = 1000) -> MetaUsage:
    return MetaUsage(world.rt.db, rates_inr=RATES, free_service_per_number=free)


async def test_only_messages_we_delivered_through_the_api_count(world):
    shop = await world.make_shop()
    conv = await _conversation(world, shop)                       # the customer's own message is inbound: never billed
    await _x(world, shop, "DELETE FROM messages WHERE direction='out'")   # start from a known zero, whatever the AI replied
    await _out(world, shop, conv, 3)                               # counted
    await _out(world, shop, conv, 1, delivered_at=None, status="sent")    # never delivered: Meta does not bill it
    await _out(world, shop, conv, 1, status="failed", delivered_at=None)
    await _out(world, shop, conv, 2, sender="owner", via="business_app")  # owner typed in the Business app: free
    await _out(world, shop, conv, 1, delivered_at=OCT)             # last month
    u = await usage(world).month(shop.business_id, 2026, 11)
    [row] = u["numbers"]
    assert (row["service"], row["utility"]) == (3, 0)


async def test_the_first_free_service_messages_of_each_number_each_month_cost_nothing(world):
    shop = await world.make_shop()
    conv = await _conversation(world, shop)
    await _x(world, shop, "DELETE FROM messages WHERE direction='out'")
    await _out(world, shop, conv, 5)
    [row] = (await usage(world, free=3).month(shop.business_id, 2026, 11))["numbers"]
    assert (row["service"], row["free_service"], row["billable_service"]) == (5, 3, 2)
    assert row["estimated_cost_inr"] == pytest.approx(2 * RATES["service"])


async def test_owner_alerts_draw_on_the_same_number_and_templates_are_billed_as_utility(world):
    shop = await world.make_shop()
    conv = await _conversation(world, shop)
    await _x(world, shop, "DELETE FROM messages WHERE direction='out'")
    await _out(world, shop, conv, 2)
    await _owner_out(world, shop, kind="text")                    # inside the owner's window: a service message
    await _owner_out(world, shop, kind="template", n=2)           # outside it: an approved utility template
    await _owner_out(world, shop, kind="text", status="failed")   # not delivered: not billed
    [row] = (await usage(world, free=2).month(shop.business_id, 2026, 11))["numbers"]
    assert (row["service"], row["utility"], row["billable_service"]) == (3, 2, 1)
    assert row["estimated_cost_inr"] == pytest.approx(1 * RATES["service"] + 2 * RATES["utility"])


async def test_who_pays_follows_whose_whatsapp_account_the_number_is_on(world):
    shop = await world.make_shop()
    conv = await _conversation(world, shop)
    await _x(world, shop, "DELETE FROM messages WHERE direction='out'")
    await _out(world, shop, conv, 1)
    await _owner_out(world, shop, kind="template", number=False)  # sent from the platform's own number: we pay
    u = await usage(world, free=0).month(shop.business_id, 2026, 11)
    assert u["numbers"][0]["payer"] == "platform"                 # simulator/pilot number: on our account, no own token
    assert u["platform_sender"]["utility"] == 1
    assert u["estimated_cost_inr"]["platform_pays"] == pytest.approx(RATES["service"] + RATES["utility"])
    assert u["estimated_cost_inr"]["shop_pays"] == 0
    await _x(world, shop, "UPDATE whatsapp_numbers SET access_token_enc='\\x01'::bytea WHERE id=%s", shop.number_id)
    u = await usage(world, free=0).month(shop.business_id, 2026, 11)
    assert u["numbers"][0]["payer"] == "shop"                     # Embedded Signup: the shop's own account, Meta bills the shop
    assert u["estimated_cost_inr"]["shop_pays"] == pytest.approx(RATES["service"])


async def test_another_shops_messages_never_count(world):
    a, b = await world.make_shop("Shop A"), await world.make_shop("Shop B")
    conv_b = await _conversation(world, b)
    await _x(world, b, "DELETE FROM messages WHERE direction='out'")
    await _out(world, b, conv_b, 4)
    u = await usage(world).month(a.business_id, 2026, 11)
    assert sum(r["service"] for r in u["numbers"]) == 0


async def test_an_owner_alert_records_which_number_sent_it(world):
    shop = await world.make_shop()
    user = {"id": shop.business_user_id, "phone": shop.owner_phone, "last_inbound_at": datetime.now(UTC), "language": "en"}
    await world.rt.owner.send_to_owner(shop.business_id, user, "handoff_alert", "A customer needs you")
    [row] = await world.q(shop, "SELECT whatsapp_number_id FROM owner_messages WHERE purpose='handoff_alert'")
    assert row["whatsapp_number_id"] == shop.number_id


async def test_an_echo_from_the_business_app_is_marked_as_not_sent_by_us(world):
    shop = await world.make_shop()
    await _conversation(world, shop)
    await world.owner_replies_from_app(shop, "+919800000001", "I'll call you")
    await world.drain(["inbound.events"])
    rows = await world.q(shop, "SELECT sent_via FROM messages WHERE direction='out' AND sender='owner'")
    assert rows and {r["sent_via"] for r in rows} == {"business_app"}


async def test_the_platform_team_can_read_a_shops_usage_and_a_shop_cannot(world, client):
    shop = await world.make_shop()
    ops = await operator_token(world, client)
    r = await client.get(f"{V1}/operator/businesses/{shop.business_id}/meta-usage", params={"month": "2026-11"}, headers=hdr(ops))
    assert r.status_code == 200 and r.json()["month"] == "2026-11" and "numbers" in r.json()
    bad = await client.get(f"{V1}/operator/businesses/{shop.business_id}/meta-usage", params={"month": "11-2026"}, headers=hdr(ops))
    assert bad.status_code == 422
    owner = await login(client, shop.owner_phone)                 # a shop never sees the platform's cost view
    r = await client.get(f"{V1}/operator/businesses/{shop.business_id}/meta-usage", params={"month": "2026-11"}, headers=hdr(owner))
    assert r.status_code == 403


def test_rates_and_the_free_allowance_are_configuration():
    s = Settings.model_fields
    assert s["meta_free_service_per_number"].default == 1000
    assert "service" in s["meta_rates_inr"].default


def test_malformed_meta_rates_stop_startup():
    with pytest.raises(ValidationError, match="META_RATES_INR"):
        make_settings(META_RATES_INR='{"service": "cheap"}')
    with pytest.raises(ValidationError, match="META_RATES_INR"):
        make_settings(META_RATES_INR='{"marketing": 0.86}')         # service and utility rates are required
