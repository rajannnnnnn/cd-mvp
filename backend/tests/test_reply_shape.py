"""Replies split into several WhatsApp messages only when that is natural (founder's instruction; ADR 0026).

Since 1 Oct 2026 every delivered message is billed, so a short answer arriving as three buzzes costs three times as
much and reads worse. The rule: one message by default; several only when the answer is genuinely long, or when the
customer is themselves writing in short bursts (mirror them, as FR-CV-5 mirrors language and tone). A per-shop
ceiling is a safety valve. Regrouping never changes a word, so the price checks that ran on the writer's parts still
hold (INV-2)."""
from __future__ import annotations

from salesai.modules.agent.llm.local import LocalRulesProvider
from salesai.modules.agent.shape import shape_reply
from tests.world import Recorder, configure_fast, install_llm, settle

THREE = ["Yes, we have red sarees.", "They come in silk and cotton.", "Shall I share a few designs?"]
ONE_LONG = ["I'd love to help with that."]


def test_a_short_answer_to_one_message_goes_as_one_message():
    assert shape_reply(THREE, ["do you have red sarees?"]) == ["\n".join(THREE)]


def test_a_long_answer_may_go_as_several():
    long = ["a" * 150, "b" * 150, "c" * 150]
    assert shape_reply(long, ["tell me everything about your sarees please"], split_over_chars=320) == long


def test_a_customer_writing_in_short_bursts_gets_short_bursts_back():
    assert shape_reply(THREE, ["hi", "red saree?", "price?"]) == THREE


def test_one_long_customer_message_is_not_a_burst():
    msgs = ["hello", "I am looking for a red silk saree for my sister's wedding next month, something not too heavy please"]
    assert shape_reply(THREE, msgs, burst_chars=80) == ["\n".join(THREE)]


def test_the_ceiling_merges_overflow_into_the_last_message_and_drops_nothing():
    parts = ["one.", "two.", "three.", "four."]
    out = shape_reply(parts, ["a", "b", "c"], max_parts=2)
    assert out == ["one.", "two.\nthree.\nfour."]


def test_a_ceiling_of_one_always_sends_one_message():
    assert shape_reply(THREE, ["a", "b", "c"], max_parts=1) == ["\n".join(THREE)]


def test_regrouping_never_adds_or_drops_a_word():
    # INV-2: the checks ran on these parts; reshaping must not alter what is said
    for customer in (["hi"], ["a", "b", "c"]):
        for cap in (1, 2, 3):
            out = shape_reply(["₹1,000 for the silk one.", "Free delivery in Pune.", "Want it?"], customer, max_parts=cap)
            assert " ".join(" ".join(out).split()) == "₹1,000 for the silk one. Free delivery in Pune. Want it?"


def test_empty_parts_are_dropped():
    assert shape_reply(["", "  ", "Hello!"], ["hi"]) == ["Hello!"]
    assert shape_reply([], ["hi"]) == []


# ---------------------------------------------------------------- end to end through a real turn

def _three_part_writer(req):
    return {"parts": THREE} if req.stage == "writer" else None


async def test_one_customer_message_gets_one_whatsapp_message_back(world):
    shop = await world.make_shop()
    await configure_fast(world, shop)
    old = install_llm(world, Recorder(LocalRulesProvider(), _three_part_writer))
    try:
        await world.customer_says(shop, "+919700000101", "do you have red sarees?")
        texts = await settle(world, shop, "+919700000101")
    finally:
        world.rt.agent.llm = old
    assert texts == ["\n".join(THREE)]


async def test_a_customer_in_short_bursts_gets_short_messages_back(world):
    shop = await world.make_shop()
    await configure_fast(world, shop)
    old = install_llm(world, Recorder(LocalRulesProvider(), _three_part_writer))
    try:
        for t in ("hi", "red saree?", "any colours?"):
            await world.customer_says(shop, "+919700000102", t)
        texts = await settle(world, shop, "+919700000102", want=3)
    finally:
        world.rt.agent.llm = old
    assert texts == THREE


async def test_the_ceiling_is_a_per_shop_setting(world):
    shop = await world.make_shop()
    await configure_fast(world, shop, conv={"max_reply_parts": 1})
    old = install_llm(world, Recorder(LocalRulesProvider(), _three_part_writer))
    try:
        for t in ("hi", "red saree?", "any colours?"):
            await world.customer_says(shop, "+919700000103", t)
        texts = await settle(world, shop, "+919700000103")
    finally:
        world.rt.agent.llm = old
    assert texts == ["\n".join(THREE)]
