"""ADR 0041, end to end: the writer is told how this customer writes, it is kept with the customer, and a reply that sounds like a company is sent back
to be rewritten with the reason."""
from __future__ import annotations

from salesai.modules.agent.llm.local import LocalRulesProvider
from tests.test_conversation_flow import shop_with_catalog
from tests.world import Recorder, install_llm, settle


async def test_the_writer_is_given_the_customers_style_and_it_is_kept_with_the_customer(world):
    shop, _ = await shop_with_catalog(world)
    rec = Recorder(LocalRulesProvider())
    old = install_llm(world, rec)
    try:
        phone = "+919800070001"
        await world.customer_says(shop, phone, "hlo bhai 😂")
        await settle(world, shop, phone)
        await world.customer_says(shop, phone, "banarasi saree kitne ka hai yaar")
        await settle(world, shop, phone, want=2)
        writer = [r for r in rec.requests if r.stage == "writer"][-1].input
        assert writer["customer_style"]["register"] in ("very_casual", "casual") and writer["customer_style"]["uses_emoji"] is True
        assert writer["customer_style"]["dominant_language"] == "hinglish"
        assert writer["reply_hint"]["emoji"] == "one_is_fine" and writer["reply_hint"]["max_words"] <= 20
        planner = [r for r in rec.requests if r.stage == "planner"][-1].input
        assert planner["customer"]["style"]["register"] == writer["customer_style"]["register"]
        [cust] = await world.q(shop, "SELECT profile FROM customers")
        assert cust["profile"]["style"]["uses_emoji"] is True and cust["profile"]["style"]["messages"] >= 2
    finally:
        world.rt.agent.llm = old


async def test_a_corporate_sounding_reply_is_sent_back_with_the_reason_and_the_rewrite_is_what_goes_out(world):
    shop, _ = await shop_with_catalog(world)
    calls = {"writer": 0}

    def script(req):
        if req.stage != "writer":
            return None
        calls["writer"] += 1
        if calls["writer"] == 1:
            return {"parts": ["Dear Customer, kindly be informed that the Banarasi Silk Saree is available."]}
        return None                                           # the real (local) writer, now told what was wrong

    rec = Recorder(LocalRulesProvider(), script=script)
    old = install_llm(world, rec)
    try:
        phone = "+919800070002"
        await world.customer_says(shop, phone, "banarasi saree available?")
        texts = await settle(world, shop, phone)
        assert texts and not any("Dear Customer" in t or "kindly" in t.lower() for t in texts)
        second = [r for r in rec.requests if r.stage == "writer"][1].input
        assert any(f.startswith("voice:") for f in second["feedback"])
    finally:
        world.rt.agent.llm = old
