"""Builds the LLM routes from configuration (ADR 0034). Which vendor and model run each pipeline stage is a setting,
so switching a model, a host or a vendor is a restart, never a code change. Adding a vendor is one adapter file plus
one line in BUILDERS. Startup refuses a configuration that would run quietly wrong."""
from __future__ import annotations

from collections.abc import Callable

from salesai.config import Settings
from salesai.modules.agent.llm.anthropic_provider import AnthropicProvider
from salesai.modules.agent.llm.base import (
    PRICES,
    LLMProvider,
    LLMRequest,
    LLMResult,
    T,
    parse_prices,
    register_prices,
)
from salesai.modules.agent.llm.chain import ResilientLLM
from salesai.modules.agent.llm.local import LocalRulesProvider
from salesai.modules.agent.llm.openai_compat_provider import OpenAICompatProvider

STAGES = ("planner", "writer", "check")

BUILDERS: dict[str, Callable[[Settings], LLMProvider]] = {
    "local": lambda s: LocalRulesProvider(),
    "anthropic": lambda s: AnthropicProvider(s.anthropic_api_key, base_url=s.anthropic_base_url, timeout_s=s.llm_timeout_s),
    "openai_compat": lambda s: OpenAICompatProvider(s.openai_compat_api_key, s.openai_compat_base_url,
                                                    timeout_s=s.llm_timeout_s, json_mode=s.llm_json_mode),
}


class StageRouter:
    """Sends each request to its stage's route. Stages without their own route (owner config-by-chat) take the
    default, which is the check route: both are small structured extractions."""
    name = "router"

    def __init__(self, routes: dict[str, ResilientLLM], *, default: ResilientLLM):
        self.routes, self.default = routes, default

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        return await self.routes.get(req.stage, self.default).generate(req, schema)


def check_prices(s: Settings) -> None:
    """Every model a real vendor will run needs a price. Without one, `cost_micros_inr` records the turn as free and
    every cost and margin figure built on turn records is wrong, with no error anywhere."""
    missing = [f"LLM_MODEL_{st.upper()}={getattr(s, f'llm_model_{st}')}" for st in STAGES
               if s.llm_stage_provider(st) != "local" and getattr(s, f"llm_model_{st}") not in PRICES]
    if s.llm_fallback_provider not in ("none", "local") and s.llm_fallback_model and s.llm_fallback_model not in PRICES:
        missing.append(f"LLM_FALLBACK_MODEL={s.llm_fallback_model}")
    if missing:
        raise ValueError(f"No price known for {', '.join(missing)}. Add it to LLM_PRICES as "
                         '{"<model>": [input, output, cached_input, cache_write]} in USD per 1M tokens, '
                         "so its cost is recorded instead of silently counted as free.")


def make_llm(s: Settings) -> StageRouter:
    register_prices(parse_prices(s.llm_prices))
    check_prices(s)
    built: dict[str, LLMProvider] = {}

    def one(name: str) -> LLMProvider:
        if name not in built:
            built[name] = BUILDERS[name](s)
        return built[name]

    fallback = None if s.llm_fallback_provider == "none" else one(s.llm_fallback_provider)
    routes = {st: ResilientLLM(one(s.llm_stage_provider(st)), fallback, fallback_model=s.llm_fallback_model or None)
              for st in STAGES}
    return StageRouter(routes, default=routes["check"])

