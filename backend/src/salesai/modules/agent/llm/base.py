"""LLM provider interface (Technical Design: Contracts). Vendor choice is configuration; the pipeline
depends only on this module. Every call returns schema-validated structured output plus tokens/latency."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Estimated list prices, USD per 1M tokens (input, output). Used only for per-turn cost visibility (NFR-14).
PRICES_USD: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def cost_micros_inr(model: str, in_tokens: int, out_tokens: int, usd_inr: float = 85.0) -> int:
    """Cost in 1e-6 INR."""
    pin, pout = PRICES_USD.get(model, (0.0, 0.0))
    usd = (in_tokens * pin + out_tokens * pout) / 1_000_000
    return int(usd * usd_inr * 1_000_000)


class LLMError(Exception):
    def __init__(self, msg: str, *, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


class LLMUnavailable(LLMError):
    """Every configured provider failed or its circuit is open."""


@dataclass
class LLMRequest:
    stage: str                            # planner | writer | check
    system: str                           # versioned prompt text
    input: dict[str, Any]                 # structured, tenant-scoped, floor-free context
    model: str
    prompt_ref: str
    max_tokens: int = 1500
    messages: list[dict[str, str]] = field(default_factory=list)   # optional rendered conversation

    def render_user_content(self) -> str:
        """Provider-agnostic rendering of the structured input for vendors that read text."""
        return json.dumps(self.input, ensure_ascii=False, default=str)


@dataclass
class LLMResult(Generic[T]):
    output: T
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int

    @property
    def cost_micros(self) -> int:
        return cost_micros_inr(self.model, self.input_tokens, self.output_tokens)


class LLMProvider(Protocol):
    name: str

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]: ...


def now_ms() -> float:
    return time.perf_counter() * 1000
