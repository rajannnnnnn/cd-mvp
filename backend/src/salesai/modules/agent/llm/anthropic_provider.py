"""Anthropic Claude adapter on the official SDK. Structured output via `messages.parse` (schema-validated),
no forced tool_choice and no sampling parameters (rejected by current models). Customer text is untrusted
input: it only ever appears inside the JSON `input`, never as instructions.

Prompt caching (ADR 0027): the system prompt is identical for every shop on a prompt version, so it carries the first
cache marker and is shared platform-wide. When the request names `cache_keys`, the shop's stable context is a second
block with its own marker and the per-turn context follows unmarked. Below a model's minimum prefix (512 tokens on the
current models) a marker is silently ignored — never an error, never a write charge."""
from __future__ import annotations

from typing import Any, Literal, cast

import anthropic
from anthropic.types import MessageParam, TextBlockParam

from salesai.modules.agent.llm.base import LLMError, LLMRequest, LLMResult, T, now_ms

_CACHE: Any = {"type": "ephemeral"}
Effort = Literal["low", "medium", "high", "xhigh", "max"]
_EFFORTS = frozenset(("low", "medium", "high", "xhigh", "max"))


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, base_url: str | None = None, timeout_s: float = 30.0, max_retries: int = 1,
                 effort: Effort = "low"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=max_retries)
        self.effort = effort

    @staticmethod
    def _user_content(req: LLMRequest) -> str | list[TextBlockParam]:
        if not req.cache_keys:
            return req.render_user_content()                  # unchanged shape for requests that opt out of caching
        stable, volatile = req.render_parts()
        if stable == "{}":
            return volatile
        return [{"type": "text", "text": stable, "cache_control": _CACHE}, {"type": "text", "text": volatile}]

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        t0 = now_ms()
        system: list[TextBlockParam] = [{"type": "text", "text": req.system, "cache_control": _CACHE}]
        user = self._user_content(req)
        effort: Effort = cast(Effort, req.effort) if req.effort in _EFFORTS else self.effort
        messages: list[MessageParam] = [*req.messages, {"role": "user", "content": user}] if req.messages else [{"role": "user", "content": user}]  # type: ignore[list-item]
        try:
            resp = await self.client.messages.parse(
                model=req.model,
                max_tokens=req.max_tokens,
                system=system,
                messages=messages,
                output_format=schema,
                output_config={"effort": effort},
            )
        except anthropic.RateLimitError as e:
            raise LLMError(f"rate limited: {e}", retryable=True) from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"connection: {e}", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"api {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        except Exception as e:  # noqa: BLE001 - schema validation failures etc.
            raise LLMError(f"{type(e).__name__}: {e}", retryable=False) from e
        if getattr(resp, "stop_reason", None) == "refusal":
            raise LLMError("model refused", retryable=False)
        out = resp.parsed_output
        if out is None:
            raise LLMError("no parsed output", retryable=False)
        u = resp.usage
        read = getattr(u, "cache_read_input_tokens", None) or 0
        write = getattr(u, "cache_creation_input_tokens", None) or 0
        # Anthropic reports fresh input separately from cache reads and writes; we record all input processed.
        return LLMResult(output=out, provider=self.name, model=req.model, input_tokens=u.input_tokens + read + write,
                         output_tokens=u.output_tokens, latency_ms=int(now_ms() - t0),
                         cache_read_tokens=read, cache_write_tokens=write)
