"""A local server speaking the slice of the Meta Graph API that Embedded Signup completion uses."""
from __future__ import annotations

import socket
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeGraph:
    VERSION = "v21.0"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail: dict[str, tuple[int, str]] = {}          # step -> (status, message)
        self.good_code = "valid-code-1234567890"
        self.app = FastAPI()
        a, v = self.app, self.VERSION
        a.get(f"/{v}/oauth/access_token")(self._token)
        a.post(f"/{v}/{{waba}}/subscribed_apps")(self._subscribe)
        a.get(f"/{v}/{{pn}}")(self._lookup)
        a.post(f"/{v}/{{pn}}/register")(self._register)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(a, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base(self) -> str:
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

    def reset(self) -> None:
        self.calls.clear()
        self.fail.clear()

    def _maybe_fail(self, step: str) -> JSONResponse | None:
        if step in self.fail:
            status, msg = self.fail[step]
            return JSONResponse({"error": {"message": msg, "type": "OAuthException", "code": 100}}, status_code=status)
        return None

    async def _token(self, request: Request) -> JSONResponse:
        q = dict(request.query_params)
        self.calls.append(("code_exchange", request.url.path, q))
        if (r := self._maybe_fail("code_exchange")) is not None:
            return r
        if q.get("code") != self.good_code or not q.get("client_secret"):
            return JSONResponse({"error": {"message": "This authorization code has expired.", "code": 100}}, status_code=400)
        return JSONResponse({"access_token": "EAAG-fake-business-token", "token_type": "bearer"})

    async def _subscribe(self, waba: str, request: Request) -> JSONResponse:
        self.calls.append(("subscribe", waba, {"auth": request.headers.get("authorization", "")}))
        return self._maybe_fail("subscribe") or JSONResponse({"success": True})

    async def _lookup(self, pn: str, request: Request) -> JSONResponse:
        self.calls.append(("number_lookup", pn, dict(request.query_params)))
        return self._maybe_fail("number_lookup") or JSONResponse({"id": pn, "display_phone_number": "+91 98765 43210", "verified_name": "Fresh Basket Kirana", "quality_rating": "GREEN"})

    async def _register(self, pn: str, request: Request) -> JSONResponse:
        self.calls.append(("register", pn, await request.json()))
        return self._maybe_fail("register") or JSONResponse({"success": True})
