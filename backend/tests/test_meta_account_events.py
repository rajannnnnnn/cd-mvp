"""Meta account events reach the right shop (FR-ON-4, INV-12), in the shapes Meta really sends.

Two silent failures are pinned here. First, Meta's coexistence webhooks `account_offboarded` and
`account_reconnected` (added April 2026) arrived under fields the parser did not read, and were dropped. Second, real
account webhooks usually identify the WhatsApp Business Account rather than a phone number; the router matched
numbers only by phone-number id, and the handler then looked up an empty id and returned without a word. The old
simulator always included a phone-number id, so the tests never saw either.

The exact shape of the two new webhooks could not be checked against Meta's docs from this environment, so both
plausible shapes are accepted (a field of its own, or an event inside `account_update`); confirming with a recorded
payload is on the go-live checklist."""
from __future__ import annotations

import uuid

from salesai.modules.tenants.service import add_simulated_number
from salesai.obs import WEBHOOK_UNHANDLED
from tests.test_conversation_flow import shop_with_catalog
from tests.world import sim_texts


async def _x(world, shop, sql: str, *args) -> None:
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute(sql, args or None)


async def _own_waba(world, shop) -> str:
    """Simulated numbers all share one WABA id; give this shop its own, as Embedded Signup does in production."""
    waba = f"waba-{uuid.uuid4().hex[:10]}"
    await _x(world, shop, "UPDATE whatsapp_numbers SET waba_id=%s", waba)
    return waba


async def _meta(world, waba: str, field: str, value: dict) -> None:
    status, _ = await world.post({"object": "whatsapp_business_account",
                                  "entry": [{"id": waba, "time": 1762000000, "changes": [{"field": field, "value": value}]}]})
    assert status == 200
    await world.drain_for(1.5)


async def _status(world, shop) -> dict[str, str]:
    return {r["display_phone"]: r["status"] for r in await world.q(shop, "SELECT display_phone, status FROM whatsapp_numbers")}


async def _assert_owner_and_operator_alerted(world, shop) -> None:
    rows = [r for r in await world.sim_thread(shop.owner_phone, "platform") if r["direction"] == "to_user"]
    assert rows and rows[-1]["template_name"] == "disconnect_alert"
    async with world.rt.db.system_tx() as c:
        alerts = await (await c.execute(
            "SELECT 1 FROM operator_alerts WHERE kind='number_disconnected' AND business_id=%s", (shop.business_id,))).fetchall()
    assert alerts


async def test_meta_account_offboarded_webhook_disconnects_the_shop_and_stops_the_ai(world):
    shop, _ = await shop_with_catalog(world)
    waba = await _own_waba(world, shop)
    await _meta(world, waba, "account_offboarded", {"waba_id": waba})
    assert set((await _status(world, shop)).values()) == {"disconnected"}
    await _assert_owner_and_operator_alerted(world, shop)
    await world.customer_says(shop, "+919600000101", "hi")
    await world.drain_for(1.2)
    assert await sim_texts(world, shop, "+919600000101") == []                    # FR-ON-4: the AI stopped


async def test_meta_account_reconnected_webhook_brings_the_shop_back(world):
    shop, _ = await shop_with_catalog(world)
    waba = await _own_waba(world, shop)
    await _meta(world, waba, "account_offboarded", {})
    await _meta(world, waba, "account_reconnected", {})
    assert set((await _status(world, shop)).values()) == {"connected"}


async def test_an_account_update_naming_only_the_business_account_still_reaches_the_shop(world):
    # the real shape: entry id = WABA, no metadata.phone_number_id
    shop, _ = await shop_with_catalog(world)
    waba = await _own_waba(world, shop)
    await _meta(world, waba, "account_update", {"event": "PARTNER_REMOVED"})
    assert set((await _status(world, shop)).values()) == {"disconnected"}
    await _assert_owner_and_operator_alerted(world, shop)


async def test_offboarding_reported_as_an_event_inside_account_update_also_disconnects(world):
    shop, _ = await shop_with_catalog(world)
    waba = await _own_waba(world, shop)
    await _meta(world, waba, "account_update", {"event": "ACCOUNT_OFFBOARDED"})
    assert set((await _status(world, shop)).values()) == {"disconnected"}


async def test_an_event_naming_one_display_phone_touches_only_that_number(world):
    shop, _ = await shop_with_catalog(world)
    second = f"+9196{uuid.uuid4().int % 10**8:08d}"
    await add_simulated_number(world.rt.db, shop.business_id, second, verified_name="Second")
    waba = await _own_waba(world, shop)
    await _meta(world, waba, "account_update", {"event": "PARTNER_REMOVED", "phone_number": second.lstrip("+")})
    assert await _status(world, shop) == {shop.business_phone: "connected", second: "disconnected"}


async def test_an_event_for_an_account_we_do_not_know_alerts_and_touches_no_shop(world):
    shop, _ = await shop_with_catalog(world)
    await _own_waba(world, shop)
    stranger = f"waba-unknown-{uuid.uuid4().hex[:8]}"
    await _meta(world, stranger, "account_update", {"event": "PARTNER_REMOVED"})
    assert set((await _status(world, shop)).values()) == {"connected"}
    async with world.rt.db.system_tx() as c:
        rows = await (await c.execute(
            "SELECT 1 FROM operator_alerts WHERE kind='unknown_number' AND detail->>'waba_id'=%s", (stranger,))).fetchall()
    assert rows                                                                     # INV-12: never silent


async def test_a_webhook_field_we_do_not_handle_is_counted_not_silently_dropped(world):
    shop, _ = await shop_with_catalog(world)
    waba = await _own_waba(world, shop)
    before = WEBHOOK_UNHANDLED.labels("some_future_field")._value.get()
    await _meta(world, waba, "some_future_field", {"anything": 1})
    assert WEBHOOK_UNHANDLED.labels("some_future_field")._value.get() == before + 1
