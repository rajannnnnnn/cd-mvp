"""Anthropic Claude adapter on the official SDK. Structured output via `messages.parse` (schema-validated),
no forced tool_choice and no sampling parameters (rejected by current models). Customer text is untrusted
input: it only ever appears inside the JSON `input`, never as instructions."""
from __future__ import annotations

import anthropic

from salesai.modules.agent.llm.base import LLMError, LLMRequest, LLMResult, T, now_ms


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, base_url: str | None = None, timeout_s: float = 30.0, max_retries: int = 1,
                 effort: str = "low"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=max_retries)
        self.effort = effort

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        t0 = now_ms()
        user = req.render_user_content()
        messages = [*req.messages, {"role": "user", "content": user}] if req.messages else [{"role": "user", "content": user}]
        try:
            resp = await self.client.messages.parse(
                model=req.model,
                max_tokens=req.max_tokens,
                system=req.system,
                messages=messages,
                output_format=schema,
                output_config={"effort": self.effort},
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
        return LLMResult(output=out, provider=self.name, model=req.model, input_tokens=resp.usage.input_tokens,
                         output_tokens=resp.usage.output_tokens, latency_ms=int(now_ms() - t0))
