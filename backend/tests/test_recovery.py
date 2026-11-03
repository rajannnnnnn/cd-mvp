"""ADR 0035: the agent can be restarted, killed or have its queue wiped at any moment, and nothing waits forever. What is in
flight lives in the database; a sweep compares what the database says should be happening with what is, and re-drives the
difference: a customer message nobody is working on, a reply that was planned and never sent, a hand-off row nobody picked up."""
from __future__ import annotations

import asyncio

from salesai.modules.conversations.recovery import Reconciler
from tests.test_conversation_flow import shop_with_catalog
from tests.world import settle, sim_texts

ENTRY = ["inbound.events", "conversation.events"]


def sweeper(world, shop, **kw) -> Reconciler:
    """The suite shares one database, so each test sweeps only its own shop."""
    return Reconciler(world.rt.db, **{"stuck_after_s": 0, "retry_after_s": 0, "queued_message_s": 0, "mailbox_s": 0, "business_id": shop.business_id, **kw})


async def lose_turn_jobs(world) -> None:
    """What a wiped or lost queue looks like: the work that was waiting is simply gone."""
    async with world.rt.db.system_tx() as c:
        await c.execute("DELETE FROM jobs WHERE queue IN ('conversation.turns', 'conversation.events', 'outbound.actions', 'owner.notifications')")


async def stored_but_unanswered(world, shop, phone: str, text: str = "silk saree price?") -> None:
    await world.customer_says(shop, phone, text)
    await world.drain(ENTRY)                                       # stored; the end-of-turn check is queued
    assert await world.q(shop, "SELECT 1 FROM messages WHERE direction='in' AND NOT answered")


# ---------------------------------------------------------------- a customer message nobody is working on

async def test_a_message_whose_work_was_lost_is_picked_up_again_and_answered(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010001"
    await stored_but_unanswered(world, shop, phone)
    await lose_turn_jobs(world)                                    # the agent died and the queue went with it
    assert await sim_texts(world, shop, phone) == []
    swept = await sweeper(world, shop).sweep()
    assert swept["retriggered"] == 1
    texts = await settle(world, shop, phone)
    assert texts and "₹" in " ".join(texts)                       # the customer was answered after all


async def test_fresh_work_is_left_alone(world):
    shop, _ = await shop_with_catalog(world)
    await stored_but_unanswered(world, shop, "+919800010002")
    swept = await sweeper(world, shop, stuck_after_s=3600).sweep()
    assert swept["retriggered"] == 0                               # it is only seconds old: the normal path still has it


async def test_the_same_stuck_conversation_is_not_retriggered_on_every_sweep(world):
    shop, _ = await shop_with_catalog(world)
    await stored_but_unanswered(world, shop, "+919800010003")
    await lose_turn_jobs(world)
    s = sweeper(world, shop, retry_after_s=3600)
    assert (await s.sweep())["retriggered"] == 1
    assert (await s.sweep())["retriggered"] == 0                   # it was just re-driven; give that time to work


async def test_after_a_few_failed_recoveries_it_hands_over_to_the_owner_instead_of_staying_silent(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010004"
    await stored_but_unanswered(world, shop, phone)
    s = sweeper(world, shop, max_attempts=2)
    for _ in range(2):
        await lose_turn_jobs(world)
        assert (await s.sweep())["retriggered"] == 1
    await lose_turn_jobs(world)
    swept = await s.sweep()
    assert swept["retriggered"] == 0 and swept["gave_up"] == 1
    [h] = await world.q(shop, "SELECT reason, status FROM handoffs")
    assert h["reason"] == "system_failure" and h["status"] == "open"          # INV-12: never silent
    assert not await world.q(shop, "SELECT 1 FROM messages WHERE direction='in' AND NOT answered")
    async with world.rt.db.system_tx() as c:
        a = await (await c.execute("SELECT severity FROM operator_alerts WHERE kind='stuck_conversation' AND business_id=%s", (shop.business_id,))).fetchone()
    assert a and a["severity"] == "high"
    assert (await s.sweep())["gave_up"] == 0                       # and it does not keep doing it


async def test_a_conversation_the_ai_must_not_answer_is_never_retriggered(world):
    shop, _ = await shop_with_catalog(world, ai_enabled=False)    # INV-10: AI switched off for this shop
    await stored_but_unanswered(world, shop, "+919800010005")
    swept = await sweeper(world, shop).sweep()
    assert swept["retriggered"] == 0 and swept["gave_up"] == 0


# ---------------------------------------------------------------- a reply that was planned and never sent

async def _planned_reply(world, shop, phone: str) -> dict:
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    [t] = await world.q(shop, "SELECT id, conversation_id, conversation_version FROM turns ORDER BY created_at DESC LIMIT 1")
    return t


async def _orphan(world, shop, turn: dict, body: str) -> None:
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("""INSERT INTO messages (business_id, conversation_id, turn_id, direction, sender, kind, body, status, created_at)
                           VALUES (%s,%s,%s,'out','ai','text',%s,'queued', now() - interval '10 minutes')""",
                        (shop.business_id, turn["conversation_id"], turn["id"], body))


async def test_a_queued_reply_whose_send_was_lost_is_sent(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010006"
    turn = await _planned_reply(world, shop, phone)
    await _orphan(world, shop, turn, "Sorry for the wait, here is the rest of the answer")
    swept = await sweeper(world, shop).sweep()
    assert swept["resent"] == 1
    texts = await settle(world, shop, phone, want=2)
    assert any("Sorry for the wait" in t for t in texts)


async def test_a_lost_reply_is_not_sent_if_the_conversation_has_moved_on(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010007"
    turn = await _planned_reply(world, shop, phone)
    await _orphan(world, shop, turn, "An answer to a question that is no longer current")
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE conversations SET version = version + 1 WHERE id=%s", (turn["conversation_id"],))
    await sweeper(world, shop).sweep()
    await world.drain_for(1.5)
    assert not any("no longer current" in t for t in await sim_texts(world, shop, phone))        # INV-7 still applies to a recovered send
    assert await world.q(shop, "SELECT 1 FROM messages WHERE body LIKE 'An answer to%%' AND status='cancelled'")


async def test_a_message_is_never_sent_twice_even_if_two_sends_are_requested(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010008"
    turn = await _planned_reply(world, shop, phone)
    await _orphan(world, shop, turn, "Only once please")
    s = sweeper(world, shop)
    await s.sweep()
    await s.sweep()                                                # a second request for the same message, e.g. after a slow first one
    await world.drain_for(2)
    assert sum("Only once please" in t for t in await sim_texts(world, shop, phone)) == 1


# ---------------------------------------------------------------- the hand-offs between components

async def test_a_webhook_event_handed_to_the_agent_but_never_picked_up_is_picked_up(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010009"
    await world.customer_says(shop, phone, "silk saree price?")
    await world.drain(["inbound.events"])                          # the gateway parsed it and left it in the agent's mailbox
    assert await world.q(shop, "SELECT 1 FROM channel_events WHERE status='pending'")
    await lose_turn_jobs(world)                                    # ...and the agent's queue entry for it was lost
    swept = await sweeper(world, shop).sweep()
    assert swept["mailbox_redriven"] >= 1
    assert await settle(world, shop, phone)


async def test_an_owner_message_the_gateway_never_got_is_requested_again(world):
    shop, _ = await shop_with_catalog(world)
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE business_users SET last_inbound_at = now() WHERE id=%s", (shop.business_user_id,))
    [u] = await world.q(shop, "SELECT bu.id, bu.name, bu.last_inbound_at, a.phone, a.language FROM business_users bu JOIN accounts a ON a.id=bu.account_id WHERE bu.id=%s",
                        shop.business_user_id)
    await world.rt.owner.send_to_owner(shop.business_id, u, "handoff_alert", "A customer needs you")
    await lose_turn_jobs(world)
    swept = await sweeper(world, shop).sweep()
    assert swept["owner_messages_redriven"] == 1
    await world.drain()
    assert any("A customer needs you" in (r["body"] or "") for r in await world.sim_thread(shop.owner_phone, shop.business_phone) if r["direction"] == "to_user")


# ---------------------------------------------------------------- state that cannot be left behind

async def test_a_conversation_stuck_mid_step_is_set_back_to_idle(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010010"
    turn = await _planned_reply(world, shop, phone)
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE conversations SET state='composing', updated_at = now() - interval '1 hour' WHERE id=%s", (turn["conversation_id"],))
    swept = await sweeper(world, shop, state_stuck_s=0).sweep()
    assert swept["state_reset"] == 1
    [conv] = await world.q(shop, "SELECT state FROM conversations WHERE id=%s", turn["conversation_id"])
    assert conv["state"] == "idle"


async def test_a_sweep_on_a_healthy_system_does_nothing(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010011"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    await world.drain()
    swept = await sweeper(world, shop, stuck_after_s=60, queued_message_s=60, mailbox_s=60).sweep()
    assert not any(v for k, v in swept.items() if k != "swept")


# ---------------------------------------------------------------- a worker that dies mid-turn

async def test_a_worker_that_dies_in_the_middle_of_a_turn_loses_nothing_and_answers_once(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800010012"
    await world.customer_says(shop, phone, "silk saree price?")
    await world.drain(ENTRY)
    q = world.rt.queue
    [dead] = await q.claim(["conversation.turns"], "worker-that-dies", lease_s=1)       # it took the end-of-turn job, then the process was killed
    assert dead.spec.kind == "eot_check"
    await asyncio.sleep(1.3)
    assert await q.reap_expired() == 1                                                  # the next scheduler tick gives it back
    texts = await settle(world, shop, phone)
    assert texts
    assert len(await world.q(shop, "SELECT 1 FROM turns")) == 1                         # one decision, not two


# ---------------------------------------------------------------- who runs it

async def test_only_the_agents_scheduler_runs_the_sweep_and_it_runs_when_the_scheduler_starts(world, monkeypatch):
    from salesai.scheduler import DUTIES, Scheduler
    assert "recovery" in DUTIES["agent"] and "recovery" in DUTIES["all"]
    assert "recovery" not in DUTIES["web"] and "recovery" not in DUTIES["gateway"]
    calls = []

    async def counting() -> dict[str, int]:
        calls.append(1)
        return {}
    monkeypatch.setattr(world.rt.reconciler, "sweep", counting)
    await Scheduler(world.rt, "agent").tick_slow(tidy=False)
    await Scheduler(world.rt, "web").tick_slow(tidy=False)
    assert calls == [1]
    stop = asyncio.Event()
    sched = Scheduler(world.rt, "agent")
    run = asyncio.create_task(sched.run(stop))
    await asyncio.sleep(1.0)                                         # the very first tick happens at once, not after a wait
    stop.set()
    await asyncio.wait_for(run, 5)
    assert len(calls) >= 2
