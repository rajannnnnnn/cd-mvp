"""Provider routing with a per-provider circuit breaker and fallback (HLD: LLM provider down or slow).
If every provider fails, `LLMUnavailable` is raised and the pipeline sends a holding message + handoff."""
from __future__ import annotations

import dataclasses
import logging
import time
from typing import TypeVar

from pydantic import BaseModel

from salesai.modules.agent.llm.base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResult,
    LLMUnavailable,
)
from salesai.obs import LLM_CALLS

log = logging.getLogger("salesai.llm")
T = TypeVar("T", bound=BaseModel)


class CircuitBreaker:
    def __init__(self, threshold: int = 4, cooldown_s: float = 30.0):
        self.threshold, self.cooldown_s = threshold, cooldown_s
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def open(self) -> bool:
        if self.opened_at is None:
            return False
        return time.monotonic() - self.opened_at < self.cooldown_s   # after the cooldown it is half-open: one probe is allowed

    def record(self, ok: bool) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


class ResilientLLM:
    name = "chain"

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None, *, fallback_model: str | None = None,
                 threshold: int = 4, cooldown_s: float = 30.0):
        self.providers = [p for p in (primary, fallback) if p is not None]
        # A fallback on another vendor cannot run the primary's model name, so it carries its own (ADR 0034).
        self.fallback_model = fallback_model or None
        # One breaker per position, not per vendor: a primary and its fallback may be the same vendor (another
        # model on the same host), and a shared breaker would let the backup's successes keep a dead primary "closed".
        self.breakers = [CircuitBreaker(threshold, cooldown_s) for _ in self.providers]

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        errors: list[str] = []
        for i, p in enumerate(self.providers):
            br = self.breakers[i]
            if br.open:
                errors.append(f"{p.name}: circuit open")
                continue
            r = dataclasses.replace(req, model=self.fallback_model) if i > 0 and self.fallback_model else req
            try:
                res = await p.generate(r, schema)
                br.record(True)
                LLM_CALLS.labels(p.name, req.stage, "ok").inc()
                return res
            except LLMError as e:
                br.record(False)
                LLM_CALLS.labels(p.name, req.stage, "error").inc()
                errors.append(f"{p.name}: {e}")
                log.warning("llm provider %s failed at %s: %s", p.name, req.stage, e)
        raise LLMUnavailable("; ".join(errors) or "no providers configured")
