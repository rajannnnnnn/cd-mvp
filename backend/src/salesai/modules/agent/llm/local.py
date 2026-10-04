"""LocalRulesProvider: deterministic stand-in for an LLM, behind the same LLMProvider interface.
Selected by LLM_PROVIDER=local (development, tests, demos without API keys). Not allowed in production."""
from __future__ import annotations

import re
from typing import Any

from salesai.modules.agent.llm import local_config, local_nlg, local_nlu
from salesai.modules.agent.llm.base import LLMError, LLMRequest, LLMResult, T, now_ms

HUMAN_CLAIM = re.compile(r"\b(i am|i'm|main|mai) (a )?(real )?(human|person|insaan|man|woman)\b|मैं (एक )?(इंसान|इन्सान)", re.I)


def check(inp: dict[str, Any]) -> dict[str, Any]:
    draft = " ".join(inp.get("parts", []))
    return {"scope_ok": True, "claims_human": bool(HUMAN_CLAIM.search(draft)), "invented_claims": [], "issues": []}


class LocalRulesProvider:
    name = "local"

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        t0 = now_ms()
        try:
            if req.stage == "planner":
                raw = local_nlu.plan(req.input)
            elif req.stage == "writer":
                raw = local_nlg.write(req.input)
            elif req.stage == "check":
                raw = check(req.input)
            elif req.stage == "config":
                raw = local_config.extract(req.input)
            else:
                raise LLMError(f"unknown stage {req.stage}", retryable=False)
            out = schema.model_validate(raw)
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"local provider failed: {type(e).__name__}: {e}", retryable=False) from e
        return LLMResult(output=out, provider=self.name, model="local-rules-v1", input_tokens=0, output_tokens=0,
                         latency_ms=int(now_ms() - t0))
