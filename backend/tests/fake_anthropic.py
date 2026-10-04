"""A tiny local server that speaks the Anthropic Messages wire format, so the REAL SDK and our adapter are exercised
over real HTTP without a vendor account or key. Responses are scripted per test; every request body is recorded."""
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


class FakeAnthropic:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.script: deque[tuple[int, dict[str, Any]]] = deque()
        self.app = FastAPI()
        self.app.post("/v1/messages")(self._messages)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

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

    def reply_json(self, obj: Any, *, input_tokens: int = 120, output_tokens: int = 40, stop_reason: str = "end_turn") -> None:
        self.script.append((200, {
            "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-fake",
            "content": [{"type": "text", "text": json.dumps(obj)}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}))

    def reply_text(self, text: str, *, stop_reason: str = "end_turn") -> None:
        self.script.append((200, {
            "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-fake",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}}))

    def reply_error(self, status: int, kind: str, message: str = "boom") -> None:
        self.script.append((status, {"type": "error", "error": {"type": kind, "message": message}}))

    async def _messages(self, request: Request) -> JSONResponse:
        self.requests.append(await request.json())
        self.headers.append({k.lower(): v for k, v in request.headers.items()})
        status, body = self.script.popleft() if self.script else (500, {"type": "error", "error": {"type": "api_error", "message": "no scripted response"}})
        return JSONResponse(body, status_code=status)
