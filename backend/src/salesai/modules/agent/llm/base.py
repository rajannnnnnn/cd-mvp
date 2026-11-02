"""LLM provider interface (Technical Design: Contracts). Vendor choice is configuration; the pipeline
depends only on this module. Every call returns schema-validated structured output plus tokens/latency."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens. `cached_input` is what a prefix-cache hit costs; `cache_write` what storing one costs."""
    input: float
    output: float
    cached_input: float
    cache_write: float


# Published list prices, Nov 2026 (NFR-14, ADR 0034). Prices are data: a new model or a host's different rate is added
# with LLM_PRICES, not here. Anthropic reads cost 0.1x input (0.05x on Opus 5.5) and five-minute writes 1.25x;
# Groq halves cached gpt-oss input and charges nothing extra to write. gpt-oss rates differ by host.
BUILTIN_PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": ModelPrice(0.10, 0.50, 0.01, 0.125),
    "claude-haiku-4-5": ModelPrice(1.00, 5.00, 0.10, 1.25),          # superseded; kept so old turn records still price
    "openai/gpt-oss-20b": ModelPrice(0.075, 0.30, 0.0375, 0.075),     # Groq list price
}
PRICES: dict[str, ModelPrice] = dict(BUILTIN_PRICES)


def parse_prices(raw: str) -> dict[str, ModelPrice]:
    """LLM_PRICES: {"model": [input, output, cached_input, cache_write]}, USD per 1M tokens. Raises ValueError."""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM_PRICES is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("LLM_PRICES must be an object of model -> [input, output, cached_input, cache_write]")
    out: dict[str, ModelPrice] = {}
    for model, row in data.items():
        if not (isinstance(row, list) and len(row) == 4 and all(isinstance(x, int | float) and x >= 0 for x in row)):
            raise ValueError(f"LLM_PRICES[{model!r}] must be four non-negative numbers: input, output, cached_input, cache_write")
        out[str(model)] = ModelPrice(*(float(x) for x in row))
    return out


def register_prices(extra: dict[str, ModelPrice]) -> None:
    PRICES.update(extra)


def cost_micros_inr(model: str, in_tokens: int, out_tokens: int, usd_inr: float = 85.0, *,
                    cache_read: int = 0, cache_write: int = 0) -> int:
    """Cost in 1e-6 INR. `in_tokens` is ALL input processed; the cached share is priced at the model's cache rates.
    An unpriced model costs 0 here, which is why the factory refuses to start with one configured."""
    p = PRICES.get(model)
    if p is None:
        return 0
    fresh = max(in_tokens - cache_read - cache_write, 0)
    usd = (fresh * p.input + cache_read * p.cached_input + cache_write * p.cache_write + out_tokens * p.output) / 1_000_000
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
    # Top-level input keys that are identical across turns for one shop (ADR 0027). They are rendered first and
    # deterministically so a provider's prefix cache can hit; everything per-turn follows them.
    cache_keys: tuple[str, ...] = ()
    effort: str | None = None              # low | medium | high; None leaves the provider's default

    def render_user_content(self) -> str:
        """Provider-agnostic rendering of the structured input for vendors that read text."""
        return json.dumps(self.input, ensure_ascii=False, default=str)

    def render_parts(self) -> tuple[str, str]:
        """(stable, volatile). The stable part has sorted keys and no whitespace so it is byte-identical however the
        database returned the rows; the two parts together are exactly `input`, no key in both."""
        stable = {k: self.input[k] for k in self.cache_keys if k in self.input}
        volatile = {k: v for k, v in self.input.items() if k not in stable}
        return (json.dumps(stable, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":")),
                json.dumps(volatile, ensure_ascii=False, default=str))


@dataclass
class LLMResult(Generic[T]):
    output: T
    provider: str
    model: str
    input_tokens: int                      # all input processed: fresh + cache reads + cache writes
    output_tokens: int
    latency_ms: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def cost_micros(self) -> int:
        return cost_micros_inr(self.model, self.input_tokens, self.output_tokens,
                               cache_read=self.cache_read_tokens, cache_write=self.cache_write_tokens)


class LLMProvider(Protocol):
    name: str

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]: ...


def now_ms() -> float:
    return time.perf_counter() * 1000
