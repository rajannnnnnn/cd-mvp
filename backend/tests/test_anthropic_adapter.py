"""The Anthropic adapter against a local server that speaks the real Messages wire format (no vendor account, no
mock of our own code): request shape, structured-output parsing, token accounting, and every failure class."""
from __future__ import annotations

import json

import pytest

from salesai.modules.agent.llm.anthropic_provider import AnthropicProvider
from salesai.modules.agent.llm.base import LLMError, LLMRequest
from salesai.modules.agent.models import PlannerOutput
from tests.fake_anthropic import FakeAnthropic

VALID_PLAN = {"intent": "product_inquiry", "lead_stage": "exploring", "language": "en", "rationale": "asked about sarees"}


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


def provider(fake: FakeAnthropic, **kw) -> AnthropicProvider:
    return AnthropicProvider("sk-ant-test-key-not-real", base_url=fake.base_url, max_retries=kw.pop("max_retries", 1), **kw)


def req(**kw) -> LLMRequest:
    return LLMRequest("planner", "You plan sales turns.", {"messages": [{"from": "customer", "text": "saree dikhao"}]}, "claude-sonnet-5-5", "planner.v1", **kw)


async def test_sends_a_well_formed_messages_request_and_parses_structured_output(api):
    api.reply_json(VALID_PLAN, input_tokens=321, output_tokens=45)
    res = await provider(api).generate(req(), PlannerOutput)
    assert res.output.intent == "product_inquiry" and res.output.lead_stage == "exploring"
    assert (res.input_tokens, res.output_tokens, res.provider, res.model) == (321, 45, "anthropic", "claude-sonnet-5-5")
    [body] = api.requests
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 1500
    assert "You plan sales turns." in json.dumps(body["system"])
    assert body["messages"][-1]["role"] == "user"
    # customer text is untrusted input: it only ever travels inside the JSON `input`, never as instructions
    assert json.loads(body["messages"][-1]["content"]) == {"messages": [{"from": "customer", "text": "saree dikhao"}]}
    assert api.headers[0]["x-api-key"] == "sk-ant-test-key-not-real"
    assert "tools" not in body and "tool_choice" not in body and "temperature" not in body      # nothing current models reject
    assert "output_config" in body                                                               # structured output + effort


async def test_the_api_key_never_appears_in_the_request_body(api):
    api.reply_json(VALID_PLAN)
    await provider(api).generate(req(), PlannerOutput)
    assert "sk-ant" not in json.dumps(api.requests[0])


async def test_rate_limit_is_retryable(api):
    for _ in range(2):
        api.reply_error(429, "rate_limit_error", "slow down")
    with pytest.raises(LLMError) as e:
        await provider(api, max_retries=1).generate(req(), PlannerOutput)
    assert e.value.retryable and "rate" in str(e.value).lower()
    assert len(api.requests) == 2                          # the SDK retried once before giving up


async def test_server_errors_are_retryable_and_client_errors_are_not(api):
    api.reply_error(529, "overloaded_error")
    api.reply_error(529, "overloaded_error")
    with pytest.raises(LLMError) as e:
        await provider(api, max_retries=1).generate(req(), PlannerOutput)
    assert e.value.retryable
    api.reset()
    api.reply_error(400, "invalid_request_error", "bad field")
    with pytest.raises(LLMError) as e2:
        await provider(api).generate(req(), PlannerOutput)
    assert not e2.value.retryable and "400" in str(e2.value)


async def test_a_transient_error_is_absorbed_by_the_sdk_retry(api):
    api.reply_error(500, "api_error")
    api.reply_json(VALID_PLAN)
    res = await provider(api, max_retries=2).generate(req(), PlannerOutput)
    assert res.output.intent == "product_inquiry" and len(api.requests) == 2


async def test_malformed_model_output_is_a_non_retryable_failure(api):
    api.reply_text("Sure! Here you go: {not json")
    with pytest.raises(LLMError) as e:
        await provider(api).generate(req(), PlannerOutput)
    assert not e.value.retryable


async def test_output_that_violates_the_schema_is_rejected(api):
    api.reply_json({**VALID_PLAN, "intent": "sell_everything_for_free"})
    with pytest.raises(LLMError):
        await provider(api).generate(req(), PlannerOutput)


async def test_a_refusal_is_reported_not_retried(api):
    api.reply_text("{}", stop_reason="refusal")
    with pytest.raises(LLMError) as e:
        await provider(api).generate(req(), PlannerOutput)
    assert not e.value.retryable


async def test_connection_failure_is_retryable():
    p = AnthropicProvider("sk-ant-test-key-not-real", base_url="http://127.0.0.1:9", max_retries=0, timeout_s=2)
    with pytest.raises(LLMError) as e:
        await p.generate(req(), PlannerOutput)
    assert e.value.retryable
