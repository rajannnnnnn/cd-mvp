"""ADR 0037: every time the AI is woken is a recorded run: what woke it (turn detection or the scheduler), how it routed and decided,
what its model calls cost in total, how long it took from the customer's message to the last reply, and how it ended: replied,
reacted, chose silence, was not allowed to answer, was overtaken by a newer message, or failed. Customer words and floor prices
never appear in it."""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace

import pytest

from salesai.modules.agent.llm.local import LocalRulesProvider
from salesai.modules.conversations.recovery import Reconciler
from salesai.scheduler import Scheduler
from tests.test_api import V1, hdr, operator_token
from tests.test_conversation_flow import shop_with_catalog
from tests.world import install_llm, settle

AU = f"{V1}/operator/audit/agent"
@pytest.fixture(autouse=True)
def _restore_llm(world):
    old = world.rt.agent.llm
    yield
    world.rt.agent.llm = old


PER_CALL_MICROS = 1_000                      # every model call in these tests costs ₹0.001, so totals are easy to check


class Costed:
    """The local provider with a fixed price and a hook, so cost and timing can be asserted and a turn can be interrupted mid-way."""
    name = "costed"

    def __init__(self, on_call=None, fail_stage: str | None = None):
        self.inner, self.on_call, self.fail_stage, self.calls = LocalRulesProvider(), on_call, fail_stage, []

    async def generate(self, req, schema):
        self.calls.append(req.stage)
        if self.fail_stage == req.stage:
            raise RuntimeError("boom")
        res = await self.inner.generate(req, schema)
        res = replace(res, model="gpt-oss-20b", input_tokens=1000, output_tokens=200, latency_ms=50)
        if self.on_call:
            await self.on_call(req.stage)
        return res


def price_model(world, monkeypatch):
    from salesai.modules.agent.llm import base
    monkeypatch.setattr(base, "cost_micros_inr", lambda *a, **k: PER_CALL_MICROS)


async def runs(world, shop):
    async with world.rt.db.tenant(shop.business_id) as c:
        return await (await c.execute("SELECT * FROM agent_runs ORDER BY started_at, id")).fetchall()


async def clear_jobs(world):
    async with world.rt.db.system_tx() as c:
        await c.execute("DELETE FROM jobs WHERE queue IN ('conversation.turns','outbound.actions')")


# ---------------------------------------------------------------- a normal reply

async def test_a_reply_is_one_run_with_its_trigger_route_cost_and_timing(world, client, monkeypatch):
    price_model(world, monkeypatch)
    shop, _ = await shop_with_catalog(world)
    install_llm(world, Costed())
    phone = "+919800020001"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    [r] = await runs(world, shop)
    assert r["trigger"] == "turn_detection" and r["status"] == "finished" and r["outcome"] == "replied"
    assert r["trigger_detail"]["eot_reason"] and r["trigger_detail"]["messages"] == 1 and r["trigger_detail"]["waited_ms"] >= 0
    assert r["route"]["intent"] and isinstance(r["route"]["directives"], list) and r["route"]["lead_stage"]
    llm = r["llm"]
    assert llm["calls"] >= 2 and llm["failed"] == 0 and set(llm["by_stage"]) >= {"planner", "writer"}
    assert llm["cost_micros"] == llm["calls"] * PER_CALL_MICROS == sum(s["cost_micros"] for s in llm["by_stage"].values())
    [turn] = await world.q(shop, "SELECT cost_micros FROM turns")
    assert turn["cost_micros"] == llm["cost_micros"]                       # the audit and the turn record agree to the micro-rupee
    op = await operator_token(world, client)
    d = (await client.get(f"{AU}/runs/{r['id']}", headers=hdr(op))).json()
    assert d["timing"]["agent_ms"] >= 0 and d["timing"]["first_reply_ms"] > 0 and d["timing"]["done_ms"] >= d["timing"]["first_reply_ms"]
    kinds = [e["type"] for e in d["timeline"]]
    assert kinds[0] == "customer_message" and "triggered" in kinds and "run_started" in kinds and kinds.count("model_call") == llm["calls"]
    assert "decision" in kinds and "message_sent" in kinds and kinds[-1] in ("message_sent", "run_finished")
    assert [e["at"] for e in d["timeline"]] == sorted(e["at"] for e in d["timeline"])


async def test_a_nudge_from_the_scheduler_is_recorded_as_such(world, monkeypatch):
    price_model(world, monkeypatch)
    shop, _ = await shop_with_catalog(world)
    install_llm(world, Costed())
    phone = "+919800020002"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE conversations SET last_outbound_at = now() - interval '5 hours', last_inbound_at = now() - interval '5 hours 10 minutes'")
    await world.drain()
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE agent_timers SET due_at = now() - interval '1 minute' WHERE kind='follow_back' AND status='pending'")
    assert await Scheduler(world.rt, "agent").tick_timers() == 1
    await settle(world, shop, phone, want=2)
    nudge = [r for r in await runs(world, shop) if r["trigger"] == "follow_back"]
    assert len(nudge) == 1 and nudge[0]["outcome"] == "replied" and nudge[0]["trigger_detail"]["nudge"] is True
    assert nudge[0]["first_inbound_at"] is None                          # nothing from the customer woke it


# ---------------------------------------------------------------- when there is no reply, the audit says why

async def test_a_chat_the_ai_may_not_answer_is_a_blocked_run_with_the_rule_and_no_model_cost(world):
    shop, _ = await shop_with_catalog(world, ai_enabled=False)
    await world.customer_says(shop, "+919800020003", "silk saree price?")
    await world.drain_for(1.5)
    [r] = await runs(world, shop)
    assert r["outcome"] == "blocked" and r["reason"] == "ai_disabled" and r["status"] == "finished"
    assert r["llm"].get("calls", 0) == 0 and r["llm"].get("cost_micros", 0) == 0


async def test_a_newer_message_stops_a_stale_turn_before_the_writer_and_the_cost_is_still_recorded(world, monkeypatch):
    price_model(world, monkeypatch)
    shop, _ = await shop_with_catalog(world)
    phone = "+919800020004"

    async def customer_writes_again(stage: str):
        if stage == "planner":
            async with world.rt.db.tenant(shop.business_id) as c:        # a new message lands while the planner was thinking
                await c.execute("UPDATE conversations SET version = version + 1")
    llm = Costed(on_call=customer_writes_again)
    install_llm(world, llm)
    await world.customer_says(shop, phone, "silk saree price?")
    for _ in range(30):
        await world.drain_for(0.1, ["inbound.events", "conversation.events", "conversation.turns"])
        if await runs(world, shop):
            break
    [r] = await runs(world, shop)
    assert r["outcome"] == "superseded" and "wrote again" in r["reason"]
    assert "writer" not in llm.calls                                      # cost control: the expensive step never ran for an answer nobody will see
    assert r["llm"]["calls"] == 1 and r["llm"]["cost_micros"] == PER_CALL_MICROS     # the planner's cost is not lost
    assert not await world.q(shop, "SELECT 1 FROM turns")                 # and no turn was committed


async def test_a_run_that_fails_is_recorded_with_its_error_and_the_retry_is_a_new_run(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800020005"
    install_llm(world, Costed(fail_stage="planner"))
    await world.customer_says(shop, phone, "silk saree price?")
    for _ in range(30):
        await world.drain_for(0.1, ["inbound.events", "conversation.events", "conversation.turns"])
        if await runs(world, shop):
            break
    [r] = await runs(world, shop)
    assert r["outcome"] == "failed" and r["status"] == "finished" and "boom" in r["reason"]
    await clear_jobs(world)                                               # do not leave a retry for other tests


async def test_a_run_whose_worker_died_is_marked_interrupted_by_the_sweep(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919800020006"
    await world.customer_says(shop, phone, "silk saree price?")
    await world.drain(["inbound.events", "conversation.events"])
    [conv] = await world.q(shop, "SELECT id FROM conversations")
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("""INSERT INTO agent_runs (business_id, conversation_id, trigger, status, started_at)
                           VALUES (%s,%s,'turn_detection','running', now() - interval '1 hour')""", (shop.business_id, conv["id"]))
    swept = await Reconciler(world.rt.db, business_id=shop.business_id, run_stale_s=300).sweep()
    assert swept["runs_interrupted"] == 1
    [r] = await runs(world, shop)
    assert r["status"] == "interrupted" and "stopped" in r["reason"] and r["finished_at"] is not None
    assert (await Reconciler(world.rt.db, business_id=shop.business_id, run_stale_s=300).sweep())["runs_interrupted"] == 0
    await clear_jobs(world)


# ---------------------------------------------------------------- the founder's view

async def test_the_summary_adds_up_cost_latency_and_how_runs_ended(world, client, monkeypatch):
    price_model(world, monkeypatch)
    op = await operator_token(world, client)
    shop, _ = await shop_with_catalog(world)
    install_llm(world, Costed())
    before = (await client.get(f"{AU}/summary", headers=hdr(op), params={"days": 7})).json()
    await world.customer_says(shop, "+919800020007", "silk saree price?")
    await settle(world, shop, "+919800020007")
    after = (await client.get(f"{AU}/summary", headers=hdr(op), params={"days": 7})).json()
    assert after["runs"] - before["runs"] == 1
    assert after["by_outcome"]["replied"] - before["by_outcome"]["replied"] == 1
    assert after["by_trigger"]["turn_detection"] - before["by_trigger"]["turn_detection"] == 1
    [r] = await runs(world, shop)
    assert after["cost"]["total_micros"] - before["cost"]["total_micros"] == r["llm"]["cost_micros"]
    assert after["cost"]["per_run_inr"] is not None and after["cost"]["per_reply_inr"] is not None
    assert after["latency"]["done_ms"]["p95"] is not None and after["latency"]["first_reply_ms"]["p50"] is not None
    assert {"calls", "failed"} <= set(after["llm"]) and {"planner", "writer"} <= set(after["llm"]["by_stage"])
    assert {"premature_runs", "premature_pct", "superseded_pct", "wait_ms"} <= set(after["turn_detection"])


async def test_a_run_started_while_the_customer_was_still_typing_counts_as_premature(world, client):
    op = await operator_token(world, client)
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919800020008", "hi")
    await world.drain(["inbound.events", "conversation.events"])
    [conv] = await world.q(shop, "SELECT id FROM conversations")
    before = (await client.get(f"{AU}/summary", headers=hdr(op), params={"days": 7})).json()
    async with world.rt.db.tenant(shop.business_id) as c:
        run = (await (await c.execute("""INSERT INTO agent_runs (business_id, conversation_id, trigger, status, outcome, started_at, finished_at)
                           VALUES (%s,%s,'turn_detection','finished','replied', now() - interval '60 seconds', now() - interval '58 seconds') RETURNING id""",
                                      (shop.business_id, conv["id"]))).fetchone())["id"]
        await c.execute("""INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status, created_at)
                           VALUES (%s,%s,'in','customer','text','and one more thing','received', now() - interval '55 seconds')""", (shop.business_id, conv["id"]))
    after = (await client.get(f"{AU}/summary", headers=hdr(op), params={"days": 7})).json()
    assert after["turn_detection"]["premature_runs"] - before["turn_detection"]["premature_runs"] == 1
    listing = (await client.get(f"{AU}/runs", headers=hdr(op), params={"business_id": str(shop.business_id)})).json()
    assert next(x for x in listing if x["id"] == str(run))["premature"] is True
    await clear_jobs(world)


async def test_the_list_filters_by_outcome_trigger_and_shop(world, client):
    op = await operator_token(world, client)
    a, b = await world.make_shop("Audit A"), await world.make_shop("Audit B")
    for shop, outcome, trig in ((a, "blocked", "turn_detection"), (a, "replied", "follow_back"), (b, "blocked", "turn_detection")):
        async with world.rt.db.tenant(shop.business_id) as c:
            cust = (await (await c.execute("INSERT INTO customers (business_id, wa_id) VALUES (%s,%s) RETURNING id", (shop.business_id, f"9198{uuid.uuid4().int % 10**8:08d}"))).fetchone())["id"]
            conv = (await (await c.execute("INSERT INTO conversations (business_id, customer_id, whatsapp_number_id) VALUES (%s,%s,%s) RETURNING id", (shop.business_id, cust, shop.number_id))).fetchone())["id"]
            await c.execute("INSERT INTO agent_runs (business_id, conversation_id, trigger, status, outcome, reason) VALUES (%s,%s,%s,'finished',%s,'x')", (shop.business_id, conv, trig, outcome))
    got = (await client.get(f"{AU}/runs", headers=hdr(op), params={"business_id": str(a.business_id), "outcome": "blocked"})).json()
    assert len(got) == 1 and got[0]["outcome"] == "blocked" and got[0]["business_name"] == "Audit A"
    got = (await client.get(f"{AU}/runs", headers=hdr(op), params={"business_id": str(a.business_id), "trigger": "follow_back"})).json()
    assert len(got) == 1 and got[0]["trigger"] == "follow_back"
    assert (await client.get(f"{AU}/runs", headers=hdr(op), params={"outcome": "nonsense"})).status_code == 422


# ---------------------------------------------------------------- what the audit never contains

async def test_the_audit_carries_no_customer_words_name_number_or_floor(world, client, monkeypatch):
    price_model(world, monkeypatch)
    op = await operator_token(world, client)
    shop, _ = await shop_with_catalog(world)
    install_llm(world, Costed())
    phone = "+919800020009"
    secret = f"my-secret-words-{uuid.uuid4().hex}"
    await world.customer_says(shop, phone, f"silk saree price? {secret}", name="Private Person Zed")
    await settle(world, shop, phone)
    [r] = await runs(world, shop)
    text = json.dumps([(await client.get(f"{AU}/runs/{r['id']}", headers=hdr(op))).json(),
                       (await client.get(f"{AU}/runs", headers=hdr(op))).json(), (await client.get(f"{AU}/summary", headers=hdr(op))).json(),
                       dict(r)], default=str)
    import re
    for needle in (secret, "Private Person Zed", phone.removeprefix("+")):
        assert needle not in text, needle
    assert not re.search(r"(?<![\w.-])800(\.00)?(?![\w.-])", text)           # the floor, as a number on its own (not a piece of an id)


async def test_the_audit_needs_the_admin_session_and_a_shop_sees_only_its_own_runs(world, client):
    shop, tok = await world.make_shop("Own Runs Shop"), None
    from tests.test_api import login
    tok = await login(client, shop.owner_phone)
    for path in ("/summary", "/runs"):
        assert (await client.get(f"{AU}{path}")).status_code == 401
        assert (await client.get(f"{AU}{path}", headers=hdr(tok))).status_code == 401
    other = await world.make_shop("Other Runs Shop")
    async with world.rt.db.tenant(other.business_id) as c:
        cust = (await (await c.execute("INSERT INTO customers (business_id, wa_id) VALUES (%s,'919100000001') RETURNING id", (other.business_id,))).fetchone())["id"]
        conv = (await (await c.execute("INSERT INTO conversations (business_id, customer_id, whatsapp_number_id) VALUES (%s,%s,%s) RETURNING id", (other.business_id, cust, other.number_id))).fetchone())["id"]
        await c.execute("INSERT INTO agent_runs (business_id, conversation_id, trigger, status) VALUES (%s,%s,'turn_detection','running')", (other.business_id, conv))
    assert await runs(world, shop) == []                                  # row-level security: a shop cannot see another shop's runs
    await asyncio.sleep(0)
