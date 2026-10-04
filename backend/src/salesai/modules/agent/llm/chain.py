"""Provider routing with a per-provider circuit breaker and fallback (HLD: LLM provider down or slow).
If every provider fails, `LLMUnavailable` is raised and the pipeline sends a holding message + handoff."""
from __future__ import annotations

import logging
import time
from typing import TypeVar

from pydantic import BaseModel

from salesai.modules.agent.llm.base import LLMError, LLMProvider, LLMRequest, LLMResult, LLMUnavailable
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
        if time.monotonic() - self.opened_at >= self.cooldown_s:   # half-open: allow a probe
            return False
        return True

    def record(self, ok: bool) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


class ResilientLLM:
    name = "chain"

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None, *, threshold: int = 4, cooldown_s: float = 30.0):
        self.providers = [p for p in (primary, fallback) if p is not None]
        self.breakers = {p.name: CircuitBreaker(threshold, cooldown_s) for p in self.providers}

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        errors: list[str] = []
        for p in self.providers:
            br = self.breakers[p.name]
            if br.open:
                errors.append(f"{p.name}: circuit open")
                continue
            try:
                res = await p.generate(req, schema)
                br.record(True)
                LLM_CALLS.labels(p.name, req.stage, "ok").inc()
                return res
            except LLMError as e:
                br.record(False)
                LLM_CALLS.labels(p.name, req.stage, "error").inc()
                errors.append(f"{p.name}: {e}")
                log.warning("llm provider %s failed at %s: %s", p.name, req.stage, e)
        raise LLMUnavailable("; ".join(errors) or "no providers configured")
