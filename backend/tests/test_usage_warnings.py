"""ADR 0032: a plan is a monthly cap on conversations and never a charge. The owner is told once at 80% and once at 100% of the cap, in
their own WhatsApp, and is never told about a price per conversation (there is none). The assistant keeps answering; nothing here stops it."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from tests.test_conversation_flow import shop_with_catalog


async def _on_plan(world, shop, plan: str, status: str = "active") -> datetime:
    start = datetime.now(UTC) - timedelta(days=3)
    async with world.rt.db.system_tx() as c:
        # no login may delete a money record (ADR 0050), so the plan is changed in place, and created only when the shop has none yet
        changed = await c.execute("UPDATE subscriptions SET plan=%s, status=%s, current_period_start=%s, current_period_end=%s WHERE business_id=%s",
                                  (plan, status, start, start + timedelta(days=30), shop.business_id))
        if changed.rowcount == 0:
            await c.execute(
                "INSERT INTO subscriptions (business_id, plan, status, current_period_start, current_period_end) VALUES (%s,%s,%s,%s,%s)",
                (shop.business_id, plan, status, start, start + timedelta(days=30)))
    return start


async def _conversations(world, shop, how_many: int, *, tag: str) -> None:
    """`how_many` more customers the assistant has answered (one decided turn each)."""
    async with world.rt.db.tenant(shop.business_id) as c:
        [num] = await (await c.execute("SELECT id FROM whatsapp_numbers LIMIT 1")).fetchall()
        for i in range(how_many):
            cust = (await (await c.execute("INSERT INTO customers (business_id, wa_id) VALUES (%s,%s) RETURNING id",
                                           (shop.business_id, f"9199{uuid.uuid4().int % 10**8:08d}{tag}{i}"))).fetchone())["id"]
            cv = (await (await c.execute("INSERT INTO conversations (business_id, customer_id, whatsapp_number_id) VALUES (%s,%s,%s) RETURNING id",
                                         (shop.business_id, cust, num["id"]))).fetchone())["id"]
            await c.execute("INSERT INTO turns (business_id, conversation_id, conversation_version, status) VALUES (%s,%s,1,'sent')", (shop.business_id, cv))


async def _cycle(world) -> None:
    await world.rt.billing.run_cycle()
    await world.drain(["owner.notifications", "outbound.actions"])


async def _warnings(world, shop) -> list[dict]:
    """What the owner was sent about usage, whichever sender carried it."""
    seen = []
    for sender in (shop.business_phone, "platform"):
        seen += [r for r in await world.sim_thread(shop.owner_phone, sender) if r["direction"] == "to_user" and r.get("template_name") == "usage_warning"]
    return seen


async def test_the_owner_is_told_once_at_eighty_percent_and_once_at_the_cap(world):
    shop, _ = await shop_with_catalog(world)
    await _on_plan(world, shop, "starter")                                  # 500 conversations a month
    await _conversations(world, shop, 399, tag="a")
    await _cycle(world)
    assert await _warnings(world, shop) == []                              # 399 of 500: not yet

    await _conversations(world, shop, 1, tag="b")                           # 400 = 80%
    await _cycle(world)
    first = await _warnings(world, shop)
    assert len(first) == 1 and "400" in first[0]["body"] and "500" in first[0]["body"]
    await _cycle(world)
    assert len(await _warnings(world, shop)) == 1                           # running again says nothing new

    await _conversations(world, shop, 100, tag="c")                         # 500 = the cap
    await _cycle(world)
    both = await _warnings(world, shop)
    assert len(both) == 2 and "500" in both[1]["body"]
    await _cycle(world)
    assert len(await _warnings(world, shop)) == 2                           # still only two for the month

    await _conversations(world, shop, 30, tag="d")                          # past the cap: the assistant keeps answering and nobody is nagged
    await _cycle(world)
    assert len(await _warnings(world, shop)) == 2


async def test_the_notice_never_mentions_a_charge_per_conversation(world):
    shop, _ = await shop_with_catalog(world)
    await _on_plan(world, shop, "starter")
    await _conversations(world, shop, 500, tag="e")
    await _cycle(world)
    body = " ".join(r["body"].lower() for r in await _warnings(world, shop))
    assert body and "no extra charge" in body and "invoice" not in body and "₹" not in body


async def test_a_new_billing_period_starts_the_count_again(world):
    shop, _ = await shop_with_catalog(world)
    start = await _on_plan(world, shop, "starter")
    await _conversations(world, shop, 400, tag="f")
    await _cycle(world)
    assert len(await _warnings(world, shop)) == 1
    later = start + timedelta(days=31)                                      # the next month: the same shop, a fresh period with its own count
    world.rt.billing.now = lambda: later
    try:
        async with world.rt.db.system_tx() as c:
            await c.execute("UPDATE subscriptions SET current_period_start=%s, current_period_end=%s WHERE business_id=%s",
                            (start + timedelta(days=30), start + timedelta(days=60), shop.business_id))
        await _cycle(world)
        assert len(await _warnings(world, shop)) == 1                       # nothing used yet in the new period
        async with world.rt.db.tenant(shop.business_id) as c:               # now this period's conversations are the ones counted
            await c.execute("UPDATE turns SET created_at = %s", (later - timedelta(hours=1),))
        await _cycle(world)
        assert len(await _warnings(world, shop)) == 2
    finally:
        world.rt.billing.now = lambda: datetime.now(UTC)


async def test_plans_without_a_cap_and_lapsed_shops_get_no_notice(world):
    shop, _ = await shop_with_catalog(world)
    await _on_plan(world, shop, "pilot", "pilot")                          # the founder's own pilots have no cap
    await _conversations(world, shop, 600, tag="h")
    await _cycle(world)
    assert await _warnings(world, shop) == []
    await _on_plan(world, shop, "starter", "canceled")                      # a lapsed shop is already being told something more important
    await _cycle(world)
    assert await _warnings(world, shop) == []


async def test_only_this_shops_owner_is_told_and_the_notice_is_recorded_once_per_threshold(world):
    shop, _ = await shop_with_catalog(world)
    other, _ = await shop_with_catalog(world)
    await _on_plan(world, shop, "starter")
    await _on_plan(world, other, "starter")
    await _conversations(world, shop, 450, tag="i")
    await _cycle(world)
    assert len(await _warnings(world, shop)) == 1 and await _warnings(world, other) == []
    await _cycle(world)
    rows = await world.q(shop, "SELECT threshold FROM usage_notices ORDER BY threshold")
    assert [r["threshold"] for r in rows] == [80]
    assert await world.q(other, "SELECT 1 FROM usage_notices") == []
