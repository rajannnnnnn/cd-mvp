"""End-to-end conversations through the REAL stack: signed webhook -> ingress -> queue -> router -> end-of-turn
-> agent pipeline -> pricing engine -> checks -> delivery planner -> sender -> simulated phone."""
from __future__ import annotations

import re
import uuid
from decimal import Decimal as D

import pytest

from salesai.modules.agent.llm.base import LLMError
from tests.world import (
    Recorder, add_product, configure_fast, install_llm, llm_json, LocalRulesProvider, settle, sim_texts,
)

PROFILE = {"address": "12 MG Road, Pune 411001", "hours": "Mon-Sat 10am-8pm", "payment_modes": ["UPI", "cash", "cards"],
           "delivery": "We deliver across Pune within 2 days.", "returns": "Exchange within 7 days with the bill.",
           "facts": ["We do free blouse stitching on silk sarees."]}


async def shop_with_catalog(world, **kw):
    shop = await world.make_shop(profile=PROFILE, **kw)
    await configure_fast(world, shop)
    vid = await add_product(world, shop, "Banarasi Silk Saree", "1000", floor="800", steps=3, description="Pure silk with zari border",
                            category="sarees", aliases=["sari"], availability="limited", stock=3)
    return shop, vid


async def test_greeting_gets_a_reply_from_the_assistant(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000001", "Hi")
    texts = await settle(world, shop, "+919700000001")
    assert texts and "Sharma Sarees" in texts[0]
    [turn] = await world.q(shop, "SELECT status, decision, prompt_versions, model FROM turns")
    assert turn["prompt_versions"]["planner"].startswith("planner@") and turn["decision"]["intent"] == "greeting"


async def test_price_question_quotes_exactly_the_engine_price_and_marks_read(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919700000002"
    await world.customer_says(shop, phone, "silk saree ka rate kitna hai")
    texts = await settle(world, shop, phone)
    joined = " ".join(texts)
    assert "₹1,000" in joined
    # honest urgency only because stock is genuinely limited (3 left) — and the number comes from the engine
    assert re.findall(r"₹[\d,]+", joined) == ["₹1,000"]
    rows = await world.sim_thread(phone, shop.business_phone)
    assert any(r["kind"] == "typing" for r in rows)                      # read receipt + typing indicator were scheduled and sent
    [m] = await world.q(shop, "SELECT status FROM messages WHERE direction='in'")
    assert m["status"] in ("read", "received")


async def test_negotiation_never_below_floor_and_hands_off_when_customer_insists(world):
    shop, vid = await shop_with_catalog(world)
    phone = "+919700000003"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone, want=1)
    prices = []
    n = 1
    for _ in range(6):
        await world.customer_says(shop, phone, "can you do 500 for it")
        n += 1
        texts = await settle(world, shop, phone, want=n)
        last = texts[-1]
        prices += [int(x.replace(",", "")) for x in re.findall(r"₹([\d,]+)", last)]
        h = await world.q(shop, "SELECT reason FROM handoffs")
        if h:
            break
    assert min(prices) >= 800                               # INV-3
    assert prices == sorted(prices, reverse=True)           # concedes gradually, never raises
    [h] = await world.q(shop, "SELECT reason, status FROM handoffs")
    assert h["reason"] == "below_floor" and h["status"] == "open"
    conv = await world.conv(shop, phone)
    assert conv["ai_paused_until"] is not None and conv["ai_paused_reason"] == "handoff"
    assert conv["lead_stage"] == "negotiating"


async def test_on_request_item_never_gets_a_price_from_the_ai(world):
    shop = await world.make_shop(profile=PROFILE)
    await configure_fast(world, shop)
    await add_product(world, shop, "Bridal Lehenga", None, disclosure="on_request", category="lehengas", negotiable=False)
    phone = "+919700000004"
    await world.customer_says(shop, phone, "bridal lehenga price kya hai")
    texts = await settle(world, shop, phone)
    assert texts and not re.search(r"\d{3,}", " ".join(texts)) and "₹" not in " ".join(texts)    # INV-4
    [h] = await world.q(shop, "SELECT reason FROM handoffs")
    assert h["reason"] == "on_request_price"


async def test_fragmented_messages_get_one_batched_answer(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919700000005"
    for part in ["hi", "do you have", "silk sarees?"]:
        await world.customer_says(shop, phone, part)
    texts = await settle(world, shop, phone)
    turns = await world.q(shop, "SELECT inbound_message_ids FROM turns")
    assert len(turns) == 1 and len(turns[0]["inbound_message_ids"]) == 3     # FR-CV-2: one response for one request
    assert any("silk" in t.lower() or "saree" in t.lower() for t in texts)


async def test_new_message_after_planning_discards_the_stale_reply(world):
    """INV-7: the first reply was planned and scheduled; a new customer message arrives before it is sent."""
    shop, _ = await shop_with_catalog(world)
    await configure_fast(world, shop, timing={"read_delay": {"mu": 7.0, "sigma": 0.01, "min_ms": 900, "max_ms": 1000},
                                              "max_total_delay_ms": 5000})
    phone = "+919700000006"
    await world.customer_says(shop, phone, "silk saree price?")
    for _ in range(40):                                            # run until the turn is committed (sends are ~1s away)
        await world.drain_for(0.1, ["inbound.events", "conversation.turns"])
        if await world.q(shop, "SELECT 1 FROM turns"):
            break
    assert await sim_texts(world, shop, phone) == []
    await world.customer_says(shop, phone, "actually do you open on sunday?")
    texts = await settle(world, shop, phone, timeout=10)
    joined = " ".join(texts)
    assert "₹" not in joined                                      # the answer to the OLD question was never sent
    cancelled = await world.q(shop, "SELECT 1 FROM messages WHERE direction='out' AND status='cancelled'")
    assert cancelled


async def test_owner_manual_reply_pauses_the_ai(world):
    shop, _ = await shop_with_catalog(world)
    phone = "+919700000007"
    await world.customer_says(shop, phone, "hello")
    await settle(world, shop, phone)
    n0 = len(await sim_texts(world, shop, phone))
    await world.owner_replies_from_app(shop, phone, "Hi, owner here — one moment")
    await world.drain(["inbound.events"])
    await world.customer_says(shop, phone, "silk saree price?")
    await world.drain_for(1.5)
    assert len(await sim_texts(world, shop, phone)) == n0           # INV-10: paused, recorded but not answered
    [m] = await world.q(shop, "SELECT answered FROM messages WHERE body='silk saree price?'")
    assert m["answered"] is True


async def test_stop_and_personal_and_disabled_never_get_replies(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000008", "STOP")
    await world.customer_says(shop, "+919700000008", "hello?")           # opted out stays silent
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("INSERT INTO customers (business_id, wa_id, is_personal) VALUES (%s,'919700000009',true)", (shop.business_id,))
    await world.customer_says(shop, "+919700000009", "hi there")
    off = await world.make_shop("Off", ai_enabled=False)
    await configure_fast(world, off)
    await world.customer_says(off, "+919700000010", "hi")
    await world.drain_for(1.5)
    for phone, s in (("+919700000008", shop), ("+919700000009", shop), ("+919700000010", off)):
        assert await sim_texts(world, s, phone) == []


async def test_identity_question_is_answered_honestly(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000011", "are you a real person or a bot?")
    texts = await settle(world, shop, "+919700000011")
    assert texts and re.search(r"assistant", " ".join(texts), re.I) and not re.search(r"i am (a )?(human|real person)", " ".join(texts), re.I)


async def test_out_of_scope_is_declined_and_steered_back(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000012", "what is the capital of France?")
    texts = await settle(world, shop, "+919700000012")
    assert texts and "Sharma Sarees" in texts[0] and "Paris" not in " ".join(texts)


async def test_language_and_script_mirroring(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000013", "नमस्ते")
    deva = await settle(world, shop, "+919700000013")
    assert deva and re.search(r"[ऀ-ॿ]", deva[0])
    await world.customer_says(shop, "+919700000014", "bhaiya silk saree ka daam kitna hai")
    hing = await settle(world, shop, "+919700000014")
    assert hing and not re.search(r"[ऀ-ॿ]", " ".join(hing)) and "₹1,000" in " ".join(hing)


async def test_business_info_comes_from_the_profile_and_unknowns_become_gaps(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000015", "what time do you open?")
    texts = await settle(world, shop, "+919700000015")
    assert "10am-8pm" in " ".join(texts)
    await world.customer_says(shop, "+919700000016", "do you offer gift wrapping?")
    await settle(world, shop, "+919700000016")
    gaps = await world.q(shop, "SELECT question, status FROM knowledge_gaps")
    assert gaps and "gift" in gaps[0]["question"].lower()
    [h] = await world.q(shop, "SELECT reason FROM handoffs")
    assert h["reason"] == "unknown_answer"


async def test_complaint_and_human_request_create_handoffs(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000017", "I want to talk to the owner")
    await world.customer_says(shop, "+919700000018", "the saree I got is damaged, I want a refund")
    await world.drain_for(2.5)
    reasons = sorted(h["reason"] for h in await world.q(shop, "SELECT reason FROM handoffs"))
    assert reasons == ["complaint", "customer_asked_human"]


async def test_order_flow_captures_a_deal_with_the_engine_price(world):
    shop, vid = await shop_with_catalog(world)
    phone = "+919700000019"
    await world.customer_says(shop, phone, "I want to buy the silk saree")
    t1 = await settle(world, shop, phone, want=1)
    assert any("address" in t.lower() for t in t1) and "₹1,000" in " ".join(t1)
    await world.customer_says(shop, phone, "Flat 4, Rose Apartments, near City Mall, Pune 411001")
    t2 = await settle(world, shop, phone, want=2)
    await world.customer_says(shop, phone, "yes confirm")
    t3 = await settle(world, shop, phone, want=3)
    assert "owner" in " ".join(t3).lower()
    [deal] = await world.q(shop, "SELECT kind, status, items, details, value FROM deals")
    assert deal["kind"] == "order" and deal["status"] == "pending" and deal["value"] == D("1000.00")
    assert deal["items"][0]["agreed_price"] == "1000.00" and "Rose Apartments" in deal["details"]["address"]


async def test_visit_commitment_is_captured(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000020", "I will come to the shop tomorrow")
    await settle(world, shop, "+919700000020")
    [deal] = await world.q(shop, "SELECT kind, details FROM deals")
    assert deal["kind"] == "visit" and "tomorrow" in deal["details"]["time"]


async def test_not_interested_stops_selling(world):
    shop, _ = await shop_with_catalog(world)
    await world.customer_says(shop, "+919700000021", "not interested, thanks")
    await settle(world, shop, "+919700000021")
    conv = await world.conv(shop, "+919700000021")
    assert conv["selling_stopped"] is True and conv["lead_stage"] == "lost"


# ------------------------------------------------------------------ reply checks end-to-end (INV-2, INV-9)
class HallucinatingWriter:
    """A misbehaving model: it invents a discount price, then (on regeneration) behaves or keeps misbehaving."""
    name = "scripted"

    def __init__(self, always: bool):
        self.always, self.calls = always, 0

    def script(self, req):
        if req.stage != "writer":
            return None
        self.calls += 1
        if self.always or self.calls == 1:
            return {"parts": ["Special price just for you: ₹499! Hurry, only 1 left, grab it now!"]}
        return None


async def test_invented_price_is_caught_regenerated_then_clean(world):
    shop, _ = await shop_with_catalog(world)
    bad = HallucinatingWriter(always=False)
    old = install_llm(world, Recorder(LocalRulesProvider(), bad.script))
    try:
        await world.customer_says(shop, "+919700000022", "silk saree price?")
        texts = await settle(world, shop, "+919700000022")
    finally:
        world.rt.agent.llm = old
    joined = " ".join(texts)
    assert "499" not in joined and "Hurry" not in joined and "₹1,000" in joined       # INV-2: only engine numbers go out
    [t] = await world.q(shop, "SELECT decision FROM turns")
    assert len(t["decision"]["checks"]["attempts"]) == 2 and not t["decision"]["checks"]["attempts"][0]["ok"]


async def test_persistently_bad_writer_falls_back_to_holding_message_and_handoff(world):
    shop, _ = await shop_with_catalog(world)
    old = install_llm(world, Recorder(LocalRulesProvider(), HallucinatingWriter(always=True).script))
    try:
        await world.customer_says(shop, "+919700000023", "silk saree price?")
        texts = await settle(world, shop, "+919700000023")
    finally:
        world.rt.agent.llm = old
    joined = " ".join(texts)
    assert texts and "499" not in joined and "₹" not in joined and "owner" in joined.lower()
    [h] = await world.q(shop, "SELECT reason FROM handoffs")
    assert h["reason"] == "system_failure"


class DownProvider:
    name = "down"

    async def generate(self, req, schema):
        raise LLMError("provider is down")


async def test_provider_outage_sends_holding_message_and_hands_off(world):
    shop, _ = await shop_with_catalog(world)
    old = install_llm(world, DownProvider())
    try:
        await world.customer_says(shop, "+919700000024", "hello")
        texts = await settle(world, shop, "+919700000024")
    finally:
        world.rt.agent.llm = old
    assert texts and "owner" in " ".join(texts).lower()
    [h] = await world.q(shop, "SELECT reason FROM handoffs")
    assert h["reason"] == "system_failure"                                  # INV-12: never silent


# ------------------------------------------------------------------ INV-1 end to end
def json_dumps_req(r) -> str:
    import json
    return json.dumps([r.input, r.system], default=str)


def _requests_with(rec: Recorder, needle: str):
    import json
    return [r for r in rec.requests if needle in json.dumps([r.input, r.system], default=str)]


async def test_floor_never_reaches_the_model_logs_or_records_while_negotiating(world, caplog):
    """INV-1: the floor is never in any model input, prompt, log line, turn record or API-visible field — as long
    as the negotiation has not conceded down to it. (Reaching the final step issues a price; see below.)"""
    import logging
    caplog.set_level(logging.DEBUG)
    shop = await world.make_shop(profile=PROFILE)
    await configure_fast(world, shop)
    await add_product(world, shop, "Banarasi Silk Saree", "9500", floor="7123.45", steps=4, category="sarees", round_to="1")
    rec = Recorder(LocalRulesProvider())
    old = install_llm(world, rec)
    try:
        phone = "+919700000025"
        await world.customer_says(shop, phone, "silk saree price?")
        await settle(world, shop, phone, want=1)
        await world.customer_says(shop, phone, "can you do 1000")        # one concession step: far from the floor
        await settle(world, shop, phone, want=2)
    finally:
        world.rt.agent.llm = old
    assert rec.requests
    blob = llm_json(rec) + " ".join(r.system for r in rec.requests)
    assert "7123" not in blob and "floor" not in blob.lower()
    planner_blob = " ".join(json_dumps_req(r) for r in rec.requests if r.stage == "planner")
    assert "9500" not in planner_blob and "9,500" not in planner_blob     # the planner never sees a price the assistant stated
    assert "7123" not in str(await world.q(shop, "SELECT decision, signals FROM turns"))
    assert "7123" not in str(await world.q(shop, "SELECT body, payload FROM messages"))
    assert "7123" not in str(await world.q(shop, "SELECT detail FROM audit_log"))
    assert "7123" not in str(await world.q(shop, "SELECT history FROM negotiations"))
    assert "7123" not in caplog.text


async def test_final_step_price_is_a_quote_only_in_the_writer_directive_never_the_planner(world, caplog):
    """When a negotiation legitimately concedes down to the owner's lowest price, that number is issued by the
    engine as an ordinary quote. It reaches the WRITER as a directive value, unlabelled; it never reaches the
    planner, a prompt, a log line, or any field naming it a floor (see docs/decisions/0006)."""
    import logging
    caplog.set_level(logging.DEBUG)
    shop = await world.make_shop(profile=PROFILE)
    await configure_fast(world, shop)
    await add_product(world, shop, "Banarasi Silk Saree", "9500", floor="7123.45", steps=2, category="sarees", round_to="1")
    rec = Recorder(LocalRulesProvider())
    old = install_llm(world, rec)
    try:
        phone = "+919700000026"
        await world.customer_says(shop, phone, "silk saree price?")
        await settle(world, shop, phone, want=1)
        for i in range(4):
            await world.customer_says(shop, phone, "can you do 1000")
            await settle(world, shop, phone, want=2 + i)
    finally:
        world.rt.agent.llm = old
    hit = _requests_with(rec, "7123")
    assert hit and all(r.stage == "writer" for r in hit)                  # only ever as an issued quote to the writer
    for r in hit:
        vals = [v for d in r.input["directives"] if d["type"] == "quote" for v in d["values"]]
        assert any(v["amount"] == "7123.45" and v["kind"] == "price" for v in vals)
    assert not [r for r in rec.requests if r.stage == "planner" and "7123" in str(r.input)]
    assert "floor" not in llm_json(rec).lower() and "7123" not in caplog.text
