"""Prompt caching and cost accounting (ADR 0027).

Caching only pays if the cached prefix is byte-identical from one turn to the next. Before this change the planner's
input began with `trigger` and `now` (a timestamp), so every turn's prefix differed and a cache marker would have hit
zero times. These tests pin the three things that make caching real: stable content is rendered first and
deterministically, volatile content is kept out of the cached block, and cost is computed from the cached/uncached
split the API reports. They run against the local server that speaks Anthropic's real wire format."""
from __future__ import annotations

import json

import pytest

from salesai.config import Settings
from salesai.modules.agent import pipeline
from salesai.modules.agent.llm.anthropic_provider import AnthropicProvider
from salesai.modules.agent.llm.base import PRICES, LLMRequest, cost_micros_inr
from salesai.modules.agent.models import PlannerOutput
from tests.fake_anthropic import FakeAnthropic

VALID_PLAN = {"intent": "product_inquiry", "lead_stage": "exploring", "language": "en", "rationale": "asked about sarees"}

# Things that change on every turn. None of them may ever sit in a cached block, or the cache never hits.
VOLATILE = {"trigger", "now", "conversation", "unanswered", "history", "candidates", "customer", "language", "script",
            "directives", "feedback", "first_reply", "customer_name", "reaction_emoji"}


@pytest.fixture(scope="module")
def fake():
    f = FakeAnthropic()
    f.start()
    yield f
    f.stop()


@pytest.fixture
def api(fake):
    fake.reset()
    return fake


def provider(fake: FakeAnthropic) -> AnthropicProvider:
    return AnthropicProvider("sk-ant-test-key-not-real", base_url=fake.base_url, max_retries=1)


def planner_req(*, now: str, trigger: str, profile: dict, history: list) -> LLMRequest:
    return LLMRequest(
        "planner", "You plan sales turns.",
        {"trigger": trigger, "now": now,
         "business": {"name": "Asha Sarees", "profile": profile, "sales": {"honorific": "ji"}},
         "conversation": {"lead_stage": "new", "history": history}},
        "claude-sonnet-5-5", "planner.v2", cache_keys=pipeline.PLANNER_CACHE_KEYS)


# ---------------------------------------------------------------- model slots and prices

def test_the_default_on_every_stage_is_the_founders_choice():
    # ADR 0034: openai/gpt-oss-20b everywhere until the benchmark says a stage needs a stronger model
    for stage in ("planner", "writer", "check"):
        assert Settings.model_fields[f"llm_model_{stage}"].default == "openai/gpt-oss-20b"


def test_current_models_are_priced_and_the_superseded_haiku_is_not_the_cheap_option():
    # claude-haiku-4-5 is ten times claude-haiku-5-5's price, and its 4096-token cache minimum means a ~300-token
    # check prompt could never cache on it at all. It stays priced only so old turn records still add up.
    assert (PRICES["claude-haiku-5-5"].input, PRICES["claude-haiku-5-5"].output) == (0.10, 0.50)
    assert PRICES["claude-haiku-4-5"].input == 1.0
    assert PRICES["openai/gpt-oss-20b"].input < PRICES["claude-haiku-5-5"].input


def test_groq_charges_half_for_cached_gpt_oss_input():
    p = PRICES["openai/gpt-oss-20b"]
    assert p.cached_input == pytest.approx(p.input / 2)


# ---------------------------------------------------------------- cost from the cached / uncached split

def test_without_cache_tokens_the_cost_is_unchanged():
    # back-compatible with every turn recorded before this change
    assert cost_micros_inr("claude-sonnet-5-5", 1000, 100) == int((1000 * 2.0 + 100 * 10.0) / 1e6 * 85.0 * 1e6)


def test_cache_reads_and_writes_are_priced_from_the_models_own_row():
    p = PRICES["claude-sonnet-5-5"]
    # Anthropic: reads at 0.1x input, five-minute writes at 1.25x
    assert (p.cached_input, p.cache_write) == (pytest.approx(p.input * 0.10), pytest.approx(p.input * 1.25))
    got = cost_micros_inr("claude-sonnet-5-5", 10_000, 500, cache_read=7_000, cache_write=1_000)
    usd = (2_000 * p.input + 7_000 * p.cached_input + 1_000 * p.cache_write + 500 * p.output) / 1e6
    assert got == pytest.approx(usd * 85.0 * 1e6, abs=2)


def test_reading_from_cache_is_cheaper_than_sending_the_same_tokens_fresh():
    fresh = cost_micros_inr("claude-sonnet-5-5", 5_000, 200)
    cached = cost_micros_inr("claude-sonnet-5-5", 5_000, 200, cache_read=4_500)
    assert cached < fresh / 2


def test_opus_cache_reads_use_their_own_published_price():
    # Opus 5.5 reads are $0.20/MTok — 0.05x, not the general 0.1x
    got = cost_micros_inr("claude-opus-5-5", 1_000_000, 0, cache_read=1_000_000)
    assert got == pytest.approx(0.20 * 85.0 * 1e6, abs=2)


# ---------------------------------------------------------------- rendering: what goes in the cached block

def test_the_planner_and_writer_never_cache_anything_that_changes_per_turn():
    assert not (set(pipeline.PLANNER_CACHE_KEYS) & VOLATILE), pipeline.PLANNER_CACHE_KEYS
    assert not (set(pipeline.WRITER_CACHE_KEYS) & VOLATILE), pipeline.WRITER_CACHE_KEYS
    assert pipeline.PLANNER_CACHE_KEYS and pipeline.WRITER_CACHE_KEYS


def test_the_stable_block_is_byte_identical_whatever_order_the_database_returned_keys_in():
    a = planner_req(now="t1", trigger="m", profile={"hours": "10-8", "city": "Pune"}, history=[])
    b = planner_req(now="t1", trigger="m", profile={"city": "Pune", "hours": "10-8"}, history=[])   # JSONB order varies
    assert a.render_parts()[0] == b.render_parts()[0]


def test_splitting_loses_nothing_and_adds_nothing():
    r = planner_req(now="t1", trigger="m", profile={"city": "Pune"}, history=[{"sender": "customer", "body": "hi"}])
    stable, volatile = r.render_parts()
    assert {**json.loads(stable), **json.loads(volatile)} == r.input
    assert not set(json.loads(stable)) & set(json.loads(volatile))


# ---------------------------------------------------------------- the request that goes over the wire

async def test_system_and_stable_context_carry_cache_markers_and_volatile_context_does_not(api):
    api.reply_json(VALID_PLAN)
    await provider(api).generate(planner_req(now="2026-11-02T10:00", trigger="message", profile={"city": "Pune"},
                                             history=[]), PlannerOutput)
    [body] = api.requests
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    stable, volatile = body["messages"][-1]["content"]
    assert stable["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in volatile
    assert set(json.loads(stable["text"])) == set(pipeline.PLANNER_CACHE_KEYS)
    # the timestamp and the trigger are exactly what used to make every prefix unique
    assert "2026-11-02T10:00" not in stable["text"] and "2026-11-02T10:00" in volatile["text"]


async def test_two_turns_of_one_conversation_send_the_same_cached_bytes(api):
    """The precondition for a cache hit: only the volatile block differs between turns."""
    for _ in range(2):
        api.reply_json(VALID_PLAN)
    p = provider(api)
    await p.generate(planner_req(now="10:00", trigger="message", profile={"city": "Pune"},
                                 history=[{"sender": "customer", "body": "hi"}]), PlannerOutput)
    await p.generate(planner_req(now="10:03", trigger="message", profile={"city": "Pune"},
                                 history=[{"sender": "customer", "body": "hi"}, {"sender": "ai", "body": "hello"}]),
                     PlannerOutput)
    first, second = (b["messages"][-1]["content"] for b in api.requests)
    assert first[0]["text"] == second[0]["text"]                       # cached block: identical bytes
    assert first[1]["text"] != second[1]["text"]                       # volatile block: moved on
    assert api.requests[0]["system"] == api.requests[1]["system"]


async def test_cached_tokens_are_counted_as_input_and_priced_from_the_split(api):
    api.reply_json(VALID_PLAN, input_tokens=200, output_tokens=40, cache_read_input_tokens=1_800)
    res = await provider(api).generate(planner_req(now="t", trigger="m", profile={}, history=[]), PlannerOutput)
    assert (res.input_tokens, res.cache_read_tokens, res.cache_write_tokens) == (2_000, 1_800, 0)
    assert res.cost_micros == cost_micros_inr("claude-sonnet-5-5", 2_000, 40, cache_read=1_800)
    assert res.cost_micros < cost_micros_inr("claude-sonnet-5-5", 2_000, 40)


async def test_a_request_with_no_cache_keys_is_sent_exactly_as_before(api):
    api.reply_json(VALID_PLAN)
    plain = LLMRequest("planner", "You plan sales turns.", {"messages": [{"from": "customer", "text": "saree"}]},
                       "claude-sonnet-5-5", "planner.v1")
    await provider(api).generate(plain, PlannerOutput)
    assert json.loads(api.requests[0]["messages"][-1]["content"]) == plain.input
