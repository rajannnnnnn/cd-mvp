"""A local HTTP endpoint standing in for a team chat's incoming webhook (a vendor stand-in that speaks the real wire format)."""
from __future__ import annotations

import socket
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeHook:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.status = 200
        app = FastAPI()

        @app.post("/hook")
        async def hook(req: Request) -> JSONResponse:
            self.received.append(await req.json())
            return JSONResponse({"ok": True}, status_code=self.status)

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hook"

    def start(self) -> None:
        self.thread.start()
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=3)
