"""A tiny local server that speaks the OpenAI chat-completions wire format — the format Groq, Together, Fireworks,
OpenRouter and a self-hosted vLLM all serve gpt-oss on — so the adapter is exercised over real HTTP without a vendor
account or key. Responses are scripted per test; every request body and header is recorded."""
from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeOpenAICompat:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.script: deque[tuple[int, dict[str, Any]]] = deque()
        self.app = FastAPI()
        self.app.post("/v1/chat/completions")(self._chat)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> None:
        self.thread.start()
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.1).close()
                return
            except OSError:
                time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

    # ---- scripting
    def reset(self) -> None:
        self.requests.clear()
        self.headers.clear()
        self.script.clear()

    def reply_text(self, text: str, *, prompt_tokens: int = 120, completion_tokens: int = 40, cached_tokens: int = 0,
                   finish_reason: str = "stop") -> None:
        self.script.append((200, {
            "id": "chatcmpl-fake", "object": "chat.completion", "model": "fake",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens,
                      "prompt_tokens_details": {"cached_tokens": cached_tokens}}}))

    def reply_json(self, obj: Any, *, prefix: str = "", **kw: Any) -> None:
        self.reply_text(prefix + json.dumps(obj), **kw)

    def reply_error(self, status: int, message: str = "error") -> None:
        self.script.append((status, {"error": {"message": message, "type": "api_error"}}))

    async def _chat(self, request: Request) -> JSONResponse:
        self.requests.append(await request.json())
        self.headers.append(dict(request.headers))
        status, body = self.script.popleft() if self.script else (500, {"error": {"message": "no scripted reply"}})
        return JSONResponse(body, status_code=status)
