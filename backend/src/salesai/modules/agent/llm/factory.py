"""Builds the configured LLM provider chain. Vendor and models are configuration (per deployment)."""
from __future__ import annotations

from salesai.config import Settings
from salesai.modules.agent.llm.anthropic_provider import AnthropicProvider
from salesai.modules.agent.llm.base import LLMProvider
from salesai.modules.agent.llm.chain import ResilientLLM
from salesai.modules.agent.llm.local import LocalRulesProvider


def _one(name: str, s: Settings) -> LLMProvider:
    if name == "anthropic":
        return AnthropicProvider(s.anthropic_api_key, base_url=s.anthropic_base_url, timeout_s=s.llm_timeout_s)
    return LocalRulesProvider()


def make_llm(s: Settings) -> ResilientLLM:
    primary = _one(s.llm_provider, s)
    fallback = None if s.llm_fallback_provider == "none" else _one(s.llm_fallback_provider, s)
    return ResilientLLM(primary, fallback)
