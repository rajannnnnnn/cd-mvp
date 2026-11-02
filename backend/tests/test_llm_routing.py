"""Which vendor and model run each pipeline stage is configuration (ADR 0034). Switching a model, a host or a vendor
is a settings change and a restart, never a code change — and a misconfiguration stops startup instead of running
quietly wrong. The worst quiet failure guarded here: a model with no known price would record every turn as free,
and every cost and margin figure on the founder dashboard would be wrong with no error anywhere."""
from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from salesai.config import Settings
from salesai.modules.agent.llm import base
from salesai.modules.agent.llm.base import LLMError, LLMRequest, LLMResult
from salesai.modules.agent.llm.chain import ResilientLLM
from salesai.modules.agent.llm.factory import StageRouter, make_llm
from salesai.modules.agent.models import PlannerOutput

DB = {"DATABASE_URL": "postgresql://x", "PRICING_DATABASE_URL": "postgresql://x", "SYSTEM_DATABASE_URL": "postgresql://x",
      "MIGRATION_DATABASE_URL": "postgresql://x"}
SECRETS = {"MASTER_KEY": base64.b64encode(b"k" * 32).decode(), "JWT_SECRET": "j" * 40, "OTP_SECRET": "o" * 40,
           "META_APP_SECRET": "m" * 24, "META_VERIFY_TOKEN": "v" * 24}
HOST = {"OPENAI_COMPAT_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_COMPAT_API_KEY": "gsk-test-not-real"}
PLAN = PlannerOutput.model_validate({"intent": "product_inquiry", "lead_stage": "exploring", "language": "en", "rationale": "r"})


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import os
    for name in list(os.environ):
        if name.startswith(("LLM_", "ANTHROPIC_", "OPENAI_COMPAT_")) or name in ("ENV", "ROLE", "WORKER_QUEUES"):
            monkeypatch.delenv(name, raising=False)
    saved = dict(base.PRICES)
    yield
    base.PRICES.clear()
    base.PRICES.update(saved)                 # make_llm registers LLM_PRICES; never leak them into other tests


def settings(**over: str) -> Settings:
    env = {**DB, **SECRETS, "ROLE": "all", "SIMULATOR_ENABLED": "false", **over}
    return Settings(_env_file=None, **{k.lower(): v for k, v in env.items()})  # type: ignore[arg-type]


# ---------------------------------------------------------------- defaults

def test_the_founders_default_is_gpt_oss_20b_on_every_stage_at_medium_effort():
    s = settings()
    assert [getattr(s, f"llm_model_{st}") for st in ("planner", "writer", "check")] == ["openai/gpt-oss-20b"] * 3
    assert (s.llm_effort_planner, s.llm_effort_writer) == ("medium", "medium")


# ---------------------------------------------------------------- per-stage routing

def test_a_stage_can_run_on_a_different_vendor_from_the_rest():
    s = settings(LLM_PROVIDER="openai_compat", **HOST, LLM_PROVIDER_WRITER="anthropic",
                 ANTHROPIC_API_KEY="sk-ant-test-not-real", LLM_MODEL_WRITER="claude-sonnet-5-5")
    assert (s.llm_stage_provider("planner"), s.llm_stage_provider("writer"), s.llm_stage_provider("check")) == \
        ("openai_compat", "anthropic", "openai_compat")
    router = make_llm(s)
    assert router.routes["writer"].providers[0].name == "anthropic"
    assert router.routes["planner"].providers[0].name == "openai_compat"


class _Rec:
    def __init__(self, name: str, *, fail: bool = False):
        self.name, self.fail, self.seen = name, fail, []

    async def generate(self, req: LLMRequest, schema):
        self.seen.append((req.stage, req.model))
        if self.fail:
            raise LLMError("down", retryable=True)
        return LLMResult(output=PLAN, provider=self.name, model=req.model, input_tokens=1, output_tokens=1, latency_ms=1)


def _req(stage: str = "planner") -> LLMRequest:
    return LLMRequest(stage, "sys", {}, "openai/gpt-oss-20b", "p.v1")


async def test_the_router_sends_each_stage_to_its_own_route_and_anything_else_to_the_default():
    a, b, c = _Rec("a"), _Rec("b"), _Rec("c")
    router = StageRouter({"planner": ResilientLLM(a), "writer": ResilientLLM(b)}, default=ResilientLLM(c))
    await router.generate(_req("planner"), PlannerOutput)
    await router.generate(_req("writer"), PlannerOutput)
    await router.generate(_req("config"), PlannerOutput)                  # owner config-by-chat rides the check route
    assert (a.seen, b.seen, [s for s, _ in c.seen]) == ([("planner", "openai/gpt-oss-20b")], [("writer", "openai/gpt-oss-20b")], ["config"])


async def test_the_fallback_runs_its_own_model_not_the_primarys():
    # a gpt-oss model name sent to Anthropic would fail; cross-vendor fallback must carry its own model
    primary, backup = _Rec("openai_compat", fail=True), _Rec("anthropic")
    await ResilientLLM(primary, backup, fallback_model="claude-haiku-5-5").generate(_req(), PlannerOutput)
    assert backup.seen == [("planner", "claude-haiku-5-5")]


async def test_two_providers_of_the_same_vendor_keep_separate_circuit_breakers():
    primary, backup = _Rec("anthropic", fail=True), _Rec("anthropic")
    chain = ResilientLLM(primary, backup, threshold=3)
    for _ in range(4):
        await chain.generate(_req(), PlannerOutput)                       # the backup answers every time
    assert chain.breakers[0].open and not chain.breakers[1].open          # the dead primary stops being called


# ---------------------------------------------------------------- misconfiguration stops startup

def test_an_openai_compatible_host_needs_a_base_url_and_a_key():
    with pytest.raises(ValidationError, match="OPENAI_COMPAT"):
        settings(LLM_PROVIDER="openai_compat")
    settings(LLM_PROVIDER="openai_compat", **HOST)


def test_a_vendor_used_by_any_stage_needs_its_key():
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        settings(LLM_PROVIDER_WRITER="anthropic", LLM_MODEL_WRITER="claude-sonnet-5-5")


def test_the_anthropic_route_only_takes_claude_models():
    with pytest.raises(ValidationError, match="claude"):
        settings(LLM_PROVIDER_WRITER="anthropic", ANTHROPIC_API_KEY="sk-ant-test-not-real", LLM_MODEL_WRITER="openai/gpt-oss-20b")


def test_a_model_with_no_price_stops_startup_instead_of_costing_nothing():
    s = settings(LLM_PROVIDER="openai_compat", **HOST, LLM_MODEL_WRITER="some/brand-new-model")
    with pytest.raises(ValueError, match="some/brand-new-model"):
        make_llm(s)


def test_llm_prices_adds_a_model_without_a_code_change():
    s = settings(LLM_PROVIDER="openai_compat", **HOST, LLM_MODEL_WRITER="openai/gpt-oss-120b",
                 LLM_PRICES='{"openai/gpt-oss-120b": [0.15, 0.60, 0.075, 0.15]}')
    make_llm(s)
    assert base.PRICES["openai/gpt-oss-120b"].output == 0.60


def test_malformed_llm_prices_stops_startup():
    with pytest.raises(ValidationError, match="LLM_PRICES"):
        settings(LLM_PRICES='{"some/model": [1, 2]}')
    with pytest.raises(ValidationError, match="LLM_PRICES"):
        settings(LLM_PRICES="not json")


def test_production_refuses_the_local_stand_in_on_any_stage():
    prod = {"ENV": "production", "OTP_ACCEPT_ANY": "false", "OTP_CHANNEL": "whatsapp_cloud", "PAYMENT_PROVIDER": "manual",
            "LLM_PROVIDER": "openai_compat", **HOST}
    settings(**prod)
    with pytest.raises(ValidationError, match="local"):
        settings(**prod, LLM_PROVIDER_CHECK="local")
