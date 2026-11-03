"""ADR 0039: rules before models. The planner stage tries the rules first and only the cases they are sure of, and safe for, skip the model; the
optional model check is skipped for replies that state nothing risky. Everything else still goes to the language model. The tier that decided
is recorded in the audit. Rules never decide anything about money: prices come from the pricing engine whatever tier ran."""
from __future__ import annotations

import pytest

from salesai.config import Settings
from salesai.modules.agent import pipeline as pl
from salesai.modules.agent.llm.base import LLMRequest, LLMResult
from salesai.modules.agent.llm.chain import ResilientLLM
from salesai.modules.agent.llm.factory import StageRouter, make_llm
from salesai.modules.agent.llm.local import LocalRulesProvider
from salesai.modules.agent.llm.tiered import DEFAULT_RULES_INTENTS, TieredPlanner
from salesai.modules.agent.models import PlannerOutput
from salesai.modules.agent.policy import Plan

CAND = {"variant_id": "11111111-1111-1111-1111-111111111111", "product_id": "22222222-2222-2222-2222-222222222222", "product_name": "Banarasi Silk Saree",
        "category": "sarees", "description": "Pure silk with zari border", "product_attributes": {"aliases": ["banarasi"]}, "variant_name": "Red",
        "attributes": {}, "is_default": True, "availability": "in_stock"}
OTHER = {**CAND, "variant_id": "33333333-3333-3333-3333-333333333333", "product_id": "44444444-4444-4444-4444-444444444444", "product_name": "Cotton Kurta",
         "category": "kurtas", "description": "Daily wear", "product_attributes": {"aliases": []}, "variant_name": "Blue"}


def planner_input(text: str, *, focus: str | None = None, cands=(CAND, OTHER), trigger: str = "reply") -> dict:
    return {"trigger": trigger, "conversation": {"qualification": {"focus_variant_id": focus} if focus else {}, "lead_stage": "exploring", "history": []},
            "candidates": list(cands), "unanswered": [{"text": text, "kind": "text"}], "business": {"name": "Sharma Sarees", "profile": {}}}


class Counting:
    """Stands in for the language model: counts how often it is asked and answers with a fixed planner output."""
    name = "counting-llm"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, req: LLMRequest, schema):
        self.calls.append(req.stage)
        return LLMResult(output=schema.model_validate({"intent": "unknown", "action": "reply"}), provider="openai_compat", model="gpt-oss-20b",
                         input_tokens=900, output_tokens=120, latency_ms=700)


async def run(text: str, tp: TieredPlanner, **kw):
    req = LLMRequest("planner", "sys", planner_input(text, **kw), "gpt-oss-20b", "planner.v2")
    return await tp.generate(req, PlannerOutput)


def tiered(inner=None, **kw) -> tuple[TieredPlanner, Counting]:
    llm = inner or Counting()
    return TieredPlanner(llm, LocalRulesProvider(), **kw), llm


# ---------------------------------------------------------------- what the rules decide alone

@pytest.mark.parametrize("text,intent", [
    ("hello", "greeting"), ("thanks a lot", "thanks"), ("ok bye", "goodbye"), ("are you a bot?", "identity_question"),
    ("I want to talk to the owner", "human_request"), ("this is a fraud, I want my money back", "complaint"), ("not interested", "not_interested"),
    ("what are your timings", "business_info"), ("where is your shop located", "business_info"),
])
async def test_the_rules_decide_greetings_thanks_safety_cases_and_plain_shop_facts_without_the_model(text, intent):
    tp, llm = tiered()
    res = await run(text, tp)
    assert res.output.intent == intent and llm.calls == []
    assert res.provider == "local" and res.input_tokens == 0 and res.cost_micros == 0         # recorded as the rules tier, at no cost


async def test_media_with_no_text_is_a_rules_decision_too():
    tp, llm = tiered()
    inp = planner_input("")
    inp["unanswered"] = [{"text": None, "kind": "audio"}]
    res = await tp.generate(LLMRequest("planner", "s", inp, "m", "p"), PlannerOutput)
    assert res.output.intent == "unsupported_media" and llm.calls == []


# ---------------------------------------------------------------- a named product: the rules only decide when the match is exact

async def test_a_price_ask_that_names_the_product_exactly_is_decided_by_the_rules():
    tp, llm = tiered()
    for text in ("banarasi silk saree price?", "how much is the Banarasi Silk Saree", "do you have banarasi"):
        res = await run(text, tp)
        assert res.output.intent in ("price_query", "availability", "product_inquiry") and res.output.product_refs, text
        assert llm.calls == [], text


async def test_a_follow_up_about_the_product_already_being_discussed_is_decided_by_the_rules():
    tp, llm = tiered()
    res = await run("price?", tp, focus=CAND["variant_id"])
    assert res.output.intent == "price_query" and llm.calls == []


async def test_a_vague_or_fuzzy_product_mention_goes_to_the_model():
    tp, llm = tiered()
    for text in ("saree price?", "price of the red one", "banaras silk sare cost", "what is the price"):
        await run(text, tp)
    assert llm.calls == ["planner"] * 4                          # category words, colours, typos and no product at all: a person's judgement


# ---------------------------------------------------------------- what always goes to the model

@pytest.mark.parametrize("text", [
    "can you do 500 for it", "thoda kam karo", "I will take it", "I will come tomorrow evening", "yes", "weather today?",
    "mujhe kuch alag dikhao jaise pichli baar", "what is the difference between the two",
])
async def test_negotiation_commitments_scope_calls_and_anything_unclear_go_to_the_model(text):
    tp, llm = tiered()
    await run(text, tp)
    assert llm.calls == ["planner"]


def test_the_default_allow_list_has_no_money_in_it():
    assert not DEFAULT_RULES_INTENTS & {"discount_request", "counter_offer", "order_intent", "visit_intent", "affirmation", "unknown", "smalltalk", "out_of_scope"}
    assert {"greeting", "thanks", "goodbye", "identity_question", "human_request", "complaint", "not_interested", "business_info"} <= DEFAULT_RULES_INTENTS


async def test_it_can_be_switched_off_and_narrowed():
    tp, llm = tiered(enabled=False)
    await run("hello", tp)
    assert llm.calls == ["planner"]
    tp, llm = tiered(intents=frozenset({"greeting"}))
    await run("hello", tp)
    await run("thanks", tp)
    assert llm.calls == ["planner"]                              # only greetings are the rules' now


async def test_other_stages_pass_straight_through():
    tp, llm = tiered()
    await tp.generate(LLMRequest("writer", "s", {"parts": []}, "m", "p"), type("W", (), {"model_validate": staticmethod(lambda x: x)}))
    assert llm.calls == ["writer"]


# ---------------------------------------------------------------- wiring

def settings(**kw) -> Settings:
    base = dict(role="all", master_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", jwt_secret="j" * 32, otp_secret="o" * 32, meta_app_secret="m" * 12,
                meta_verify_token="v" * 12, llm_provider="openai_compat", openai_compat_base_url="http://127.0.0.1:9/v1", openai_compat_api_key="k",
                database_url="postgresql://x/x", pricing_database_url="postgresql://x/x", system_database_url="postgresql://x/x", migration_database_url="postgresql://x/x")
    import os
    from unittest import mock
    with mock.patch.dict(os.environ, {}, clear=True):
        return Settings(_env_file=None, **{**base, **kw})


def planner_route(s: Settings):
    r = make_llm(s)
    assert isinstance(r, StageRouter)
    return r.routes["planner"]


def test_a_real_model_gets_the_rules_in_front_of_it_by_default_and_the_local_stand_in_does_not():
    assert isinstance(planner_route(settings()), TieredPlanner)
    assert isinstance(planner_route(settings(tiering_enabled=False)), ResilientLLM)
    assert isinstance(planner_route(settings(llm_provider="local")), ResilientLLM)           # nothing to put rules in front of


def test_the_allow_list_is_configuration():
    tp = planner_route(settings(tiering_planner_intents="greeting, thanks"))
    assert tp.intents == frozenset({"greeting", "thanks"})
    with pytest.raises(ValueError, match="TIERING_PLANNER_INTENTS"):
        settings(tiering_planner_intents="greeting, teleport")


# ---------------------------------------------------------------- the optional model check is for replies that say something

def plan_with(*types: str) -> Plan:
    return Plan(intent="x", directives=[{"type": t} for t in types])


def test_a_reply_that_states_nothing_risky_skips_the_model_check_and_any_reply_with_a_fact_or_a_price_keeps_it():
    for low in (("greeting",), ("thanks",), ("goodbye",), ("ack",), ("ask_product",), ("handoff_notice", "ack"), ("decline_out_of_scope",)):
        assert not pl.needs_model_check(plan_with(*low)), low
    for risky in (("quote",), ("info",), ("facts",), ("availability",), ("product_info",), ("catalog",), ("order_summary",), ("greeting", "quote"), ("visit_captured",)):
        assert pl.needs_model_check(plan_with(*risky)), risky
    assert pl.needs_model_check(plan_with("some_new_directive"))          # a directive nobody classified is treated as risky, never as safe


def test_the_settings_list_of_intents_matches_the_planners_own():
    import typing

    from salesai.config import DEFAULT_TIERING_INTENTS, KNOWN_INTENTS
    from salesai.modules.agent.models import Intent
    assert set(KNOWN_INTENTS) == set(typing.get_args(Intent))
    assert set(DEFAULT_TIERING_INTENTS) <= set(KNOWN_INTENTS)


async def test_end_to_end_the_model_check_runs_for_a_price_reply_and_is_skipped_for_a_greeting(world):
    from tests.test_conversation_flow import shop_with_catalog
    from tests.world import install_llm, settle

    class CountStages:
        name = "count"

        def __init__(self) -> None:
            self.calls: list[str] = []
            self.inner = LocalRulesProvider()

        async def generate(self, req, schema):
            self.calls.append(req.stage)
            return await self.inner.generate(req, schema)

    shop, _ = await shop_with_catalog(world)
    llm = CountStages()
    old_llm, old_flag = install_llm(world, llm), world.rt.agent.llm_checks
    world.rt.agent.llm_checks = True
    try:
        await world.customer_says(shop, "+919800060001", "hello")
        await settle(world, shop, "+919800060001")
        assert "check" not in llm.calls                                    # a greeting states nothing to check
        await world.customer_says(shop, "+919800060002", "banarasi silk saree price?")
        await settle(world, shop, "+919800060002")
        assert "check" in llm.calls                                        # a price does
    finally:
        world.rt.agent.llm, world.rt.agent.llm_checks = old_llm, old_flag
