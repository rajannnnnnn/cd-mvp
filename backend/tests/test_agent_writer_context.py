"""The writer is told whether this is the first reply, the recent history and what the customer just said, so it never re-greets mid-chat.
Found by running the real model through the LLM bridge: every turn began with "Namaste ... welcome to <shop>" because the writer saw no history."""
from __future__ import annotations

import uuid

from tests.world import Recorder, configure_fast, install_llm, settle


async def test_the_writer_knows_it_is_mid_conversation(world):
    shop = await world.make_shop(profile={"hours": "Mon-Sat 10am-8pm", "address": "Laxmi Road, Pune"})
    await configure_fast(world, shop)
    phone = f"+9199{uuid.uuid4().int % 10**8:08d}"
    rec = Recorder(world.rt.agent.llm)
    old = install_llm(world, rec)
    try:
        await world.customer_says(shop, phone, "hello")
        await settle(world, shop, phone, want=1, timeout=12)
        await world.customer_says(shop, phone, "what time does the shop open?")
        await settle(world, shop, phone, want=2, timeout=12)
    finally:
        world.rt.agent.llm = old
    writers = [r.input for r in rec.requests if r.stage == "writer"]
    assert len(writers) >= 2
    assert writers[0]["first_reply"] is True and writers[0]["history"] == []
    later = writers[-1]
    assert later["first_reply"] is False
    assert any(m["text"] == "hello" and m["sender"] == "customer" for m in later["history"])
    assert any(m["sender"] == "ai" for m in later["history"])
    assert later["customer_just_said"] == ["what time does the shop open?"]


async def test_the_turn_audit_counts_every_model_call(world):
    shop = await world.make_shop(profile={"hours": "Mon-Sat 10am-8pm"})
    await configure_fast(world, shop)
    phone = f"+9199{uuid.uuid4().int % 10**8:08d}"
    rec = Recorder(world.rt.agent.llm)
    old = install_llm(world, rec)
    try:
        await world.customer_says(shop, phone, "hello")
        await settle(world, shop, phone, want=1, timeout=12)
    finally:
        world.rt.agent.llm = old
    [turn] = await world.q(shop, "SELECT decision->'llm' AS llm FROM turns")
    llm = turn["llm"]
    assert llm["calls"] == len(rec.requests) >= 2                                  # at least the planner and the writer
    assert sum(llm["by_stage"].values()) == llm["calls"] and llm["failed"] == 0
    assert [c["stage"] for c in llm["detail"]][:2] == ["planner", "writer"]
