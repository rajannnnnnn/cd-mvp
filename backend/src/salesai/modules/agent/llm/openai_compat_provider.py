"""OpenAI-compatible chat-completions adapter (ADR 0034). The founder's default model, openai/gpt-oss-20b, is served
in this wire format by Groq, Together, Fireworks, OpenRouter and a self-hosted vLLM alike, so the host is just
OPENAI_COMPAT_BASE_URL: switching host — including to our own GPU in an Indian region — is configuration.

Defensive by design, because host behaviour varies and could not be checked against each host's docs from here:
- JSON is requested by schema (or plain JSON mode for hosts without schema support), but every reply is validated
  against the schema here regardless of what the host promises.
- A reasoning model may put its thinking in the content ahead of the answer; the last JSON object is taken.
- Stable context is rendered first, so hosts that cache by prompt prefix (Groq halves cached gpt-oss input) can hit.
Customer text is untrusted input: it only ever travels inside the JSON `input`, never as instructions."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from salesai.modules.agent.llm.base import LLMError, LLMRequest, LLMResult, T, now_ms

JsonMode = Literal["json_schema", "json_object"]


def last_json_object(text: str) -> dict[str, Any]:
    """The last top-level JSON object in `text`. Raises ValueError if there is none."""
    t = text.strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    dec, found, i = json.JSONDecoder(), None, t.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(t, i)
        except json.JSONDecodeError:
            i = t.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            found = obj
        i = t.find("{", end)                     # skip past this object so its inner braces are not re-read
    if found is None:
        raise ValueError("no JSON object in reply")
    return found


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str, *, timeout_s: float = 30.0, max_retries: int = 1,
                 json_mode: JsonMode = "json_schema", retry_base_s: float = 0.25, thinking_tokens: int = 0):
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", timeout=timeout_s,
                                        headers={"Authorization": f"Bearer {api_key}"})
        self.max_retries, self.json_mode, self.retry_base_s = max_retries, json_mode, retry_base_s
        self.thinking_tokens = max(0, thinking_tokens)      # room for a thinking model's reasoning, added to every stage's own budget

    def _body(self, req: LLMRequest, schema: type[T]) -> dict[str, Any]:
        if req.cache_keys:
            stable, volatile = req.render_parts()
            user = volatile if stable == "{}" else f"{stable}\n{volatile}"
        else:
            user = req.render_user_content()
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [{"role": "system", "content": req.system}, *req.messages, {"role": "user", "content": user}],
            "max_tokens": req.max_tokens + self.thinking_tokens,
        }
        if self.json_mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
        else:
            body["response_format"] = {"type": "json_object"}
        if req.effort:
            body["reasoning_effort"] = req.effort
        return body

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        err: LLMError = LLMError("no attempt made")
        for attempt in range(self.max_retries + 1):
            try:
                r = await self.client.post("chat/completions", json=body)
            except httpx.HTTPError as e:                      # timeouts, refused connections, resets
                err = LLMError(f"connection: {type(e).__name__}: {e}", retryable=True)
            else:
                if r.status_code == 200:
                    data: dict[str, Any] = r.json()
                    return data
                try:
                    detail = str((r.json().get("error") or {}).get("message", ""))[:200]
                except ValueError:
                    detail = r.text[:200]
                retryable = r.status_code == 429 or r.status_code >= 500
                err = LLMError(f"api {r.status_code}: {detail}", retryable=retryable)
                if not retryable:
                    raise err
            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_base_s * (2 ** attempt))
        raise err

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        t0 = now_ms()
        data = await self._post(self._body(req, schema))
        choice = (data.get("choices") or [{}])[0]
        content = str((choice.get("message") or {}).get("content") or "")
        try:
            obj = last_json_object(content)
        except ValueError as e:
            if choice.get("finish_reason") == "length":
                raise LLMError(f"reply truncated at max_tokens={req.max_tokens} for stage {req.stage}; "
                               "a reasoning model needs headroom for its thinking: raise OPENAI_COMPAT_THINKING_TOKENS (for example 2000)", retryable=False) from e
            raise LLMError("reply contained no JSON object", retryable=False) from e
        try:
            out = schema.model_validate(obj)
        except ValidationError as e:
            raise LLMError(f"reply failed the {schema.__name__} schema: {e.error_count()} error(s)", retryable=False) from e
        usage = data.get("usage") or {}
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        # OpenAI-style usage: prompt_tokens already includes the cached share
        return LLMResult(output=out, provider=self.name, model=req.model,
                         input_tokens=int(usage.get("prompt_tokens") or 0),
                         output_tokens=int(usage.get("completion_tokens") or 0),
                         latency_ms=int(now_ms() - t0), cache_read_tokens=cached)
