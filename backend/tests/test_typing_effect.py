"""The customer's experience of a reply: read ticks soon after they stop writing, 'typing...' while the answer is being worked out
(overlapping with the model's own thinking time instead of adding to it), the message after a believable typing time, and the
indicator kept alive for long ones (WhatsApp ends it after about 25 seconds). Nothing shows when the AI will say nothing."""
from __future__ import annotations

import math
import random
from dataclasses import replace

from salesai.modules.agent.llm.local import LocalRulesProvider
from salesai.modules.delivery.planner import plan_delivery
from tests.test_conversation_flow import shop_with_catalog
from tests.world import install_llm, settle

STEADY = {"read_delay": {"mu": math.log(1000), "sigma": 0, "min_ms": 1000, "max_ms": 1000},
          "typing_ms_per_char": {"mu": math.log(50), "sigma": 0, "min_ms": 50, "max_ms": 50},       # exactly 50 ms a character
          "part_gap": {"mu": math.log(800), "sigma": 0, "min_ms": 800, "max_ms": 800},
          "max_total_delay_ms": 120_000}


def plan(parts, **kw):
    return plan_delivery(parts, None, kw.pop("timing", STEADY), rng=random.Random(1), **kw)


def at(acts, action, i=None):
    return [a for a in acts if a.action == action and (i is None or a.part_index == i)]


# ---------------------------------------------------------------- the schedule itself

def test_nothing_changes_for_a_plain_reply():
    acts = plan(["x" * 40])
    assert [a.action for a in acts] == ["mark_read", "typing", "send_text"]      # nothing was showing yet, so the plan starts the indicator
    assert acts[0].delay_ms == 1000 and acts[1].delay_ms == 1000 and acts[2].delay_ms == 1000 + 40 * 50


def test_when_the_read_receipt_already_went_out_the_plan_does_not_send_another():
    acts = plan(["x" * 40], include_read=False, elapsed_ms=0)
    assert [a.action for a in acts] == ["send_text"]


def test_typing_time_already_spent_thinking_is_not_spent_again():
    # the customer has been seeing "typing..." for 3 seconds while the model wrote: only the rest of a believable typing time is added
    acts = plan(["x" * 100], include_read=False, elapsed_ms=3000)             # 100 characters is 5 s of typing
    assert at(acts, "send_text")[0].delay_ms == 2000


def test_a_slow_model_never_makes_the_reply_wait_longer_just_to_look_human():
    acts = plan(["x" * 100], include_read=False, elapsed_ms=9000)             # already past the typing time
    assert at(acts, "send_text")[0].delay_ms <= 400                           # it goes out at once (a beat, not a pause)


def test_the_second_part_still_gets_its_own_pause_and_typing():
    acts = plan(["x" * 40, "y" * 40], include_read=False, elapsed_ms=0)
    assert [a.action for a in acts] == ["send_text", "typing", "send_text"]
    t1, ty, t2 = (a.delay_ms for a in acts)
    assert t1 == 40 * 50 and ty == t1 + 800 and t2 == ty + 40 * 50


def test_a_long_message_keeps_the_typing_indicator_alive():
    acts = plan(["x" * 600], include_read=False, elapsed_ms=0)                # 30 s of typing; the indicator lasts about 25
    refresh = at(acts, "typing", 0)
    assert len(refresh) == 1 and refresh[0].delay_ms == 20_000
    assert refresh[0].delay_ms < at(acts, "send_text")[0].delay_ms
    assert not at(plan(["x" * 300], include_read=False, elapsed_ms=0), "typing")   # 15 s: no refresh needed
    long = plan(["x" * 1200], include_read=False, elapsed_ms=0)               # 60 s: refreshed every 20 s
    assert [a.delay_ms for a in at(long, "typing", 0)] == [20_000, 40_000]


def test_the_total_delay_cap_still_holds():
    capped = {**STEADY, "max_total_delay_ms": 5000}
    acts = plan(["x" * 600], timing=capped, include_read=False, elapsed_ms=0)
    assert max(a.delay_ms for a in acts) <= 5000
    assert at(acts, "send_text")[0].delay_ms == max(a.delay_ms for a in acts)


# ---------------------------------------------------------------- the flow, end to end

class Spy:
    """The local provider with a hook that runs before each model call, to see what the customer is already being shown."""
    name = "spy"

    def __init__(self, before):
        self.inner, self.before = LocalRulesProvider(), before

    async def generate(self, req, schema):
        await self.before(req.stage)
        res = await self.inner.generate(req, schema)
        return replace(res, latency_ms=50)


async def actions(world, shop) -> list[str]:
    async with world.rt.db.system_tx() as c:
        rows = await (await c.execute(
            "SELECT payload->>'action' AS a FROM outbox WHERE business_id=%s AND event_type='outbound.action_requested' ORDER BY seq", (shop.business_id,))).fetchall()
    return [r["a"] for r in rows]


async def test_the_customer_sees_read_then_typing_while_the_answer_is_still_being_written(world):
    shop, _ = await shop_with_catalog(world)
    seen: dict[str, list[str]] = {}

    async def before(stage: str) -> None:
        seen[stage] = await actions(world, shop)
    install_llm(world, Spy(before))
    phone = "+919800030001"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    assert "mark_read" in seen["planner"]                                    # read before the model has started: the customer sees ticks, not nothing
    assert "typing" not in seen["planner"]                                   # but no "typing..." yet: it may still decide to say nothing
    assert "typing" in seen["writer"] and "send_text" not in seen["writer"]  # typing starts as soon as it is going to reply, before the writing
    final = await actions(world, shop)
    assert final.index("mark_read") < final.index("typing") < final.index("send_text")
    assert final.count("mark_read") == 1                                     # one read receipt, not one at the start and another in the plan


async def test_the_typing_indicator_appears_in_the_chat_before_the_message(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800030002"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    kinds = [r["kind"] for r in await world.sim_thread(phone, shop.business_phone) if r["direction"] == "to_user"]
    assert "typing" in kinds and kinds.index("typing") < kinds.index("text")


async def test_a_chat_the_ai_may_not_answer_shows_no_read_and_no_typing(world):
    shop, _ = await shop_with_catalog(world, ai_enabled=False)
    phone = "+919800030003"
    await world.customer_says(shop, phone, "silk saree price?")
    await world.drain_for(1.5)
    assert await actions(world, shop) == []
    assert not [r for r in await world.sim_thread(phone, shop.business_phone) if r["direction"] == "to_user"]


async def test_reading_alone_does_not_show_typing_but_the_typing_action_does(world, monkeypatch):
    shop, _ = await shop_with_catalog(world)
    calls: list[tuple[str, bool]] = []

    class Chan:
        async def mark_read(self, ref, message_id, typing=True):
            calls.append((message_id, typing))
            from salesai.modules.channels.base import SendResult
            return SendResult(ok=True)

    async def number(bid, nid):
        return world.rt.channels.platform_sender()[0], Chan()
    monkeypatch.setattr(world.rt.channels, "number", number)
    async with world.rt.db.tenant(shop.business_id) as c:
        cust = (await (await c.execute("INSERT INTO customers (business_id, wa_id) VALUES (%s,'919800030004') RETURNING id", (shop.business_id,))).fetchone())["id"]
        conv = (await (await c.execute("INSERT INTO conversations (business_id, customer_id, whatsapp_number_id, last_inbound_at) VALUES (%s,%s,%s, now()) RETURNING id, version",
                                       (shop.business_id, cust, shop.number_id))).fetchone())
        from salesai.events.outbox import emit
        for action in ("mark_read", "typing"):
            await emit(c, "outbound.action_requested", {"conversation_id": conv["id"], "action": action, "version": conv["version"], "inbound_wa_id": f"wamid.{action}"},
                       business_id=shop.business_id, ordering_key=f"conv:{conv['id']}")
    await world.drain(["outbound.actions"])
    assert sorted(calls) == [("wamid.mark_read", False), ("wamid.typing", True)]
