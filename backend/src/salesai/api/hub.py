"""Live updates: Postgres LISTEN/NOTIFY fans out to this process's SSE clients (any API instance sees every change)."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from typing import Any

import psycopg

log = logging.getLogger("salesai.hub")


class ChangeHub:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
                    await conn.execute("LISTEN salesai_changes")
                    async for n in conn.notifies():
                        try:
                            d = json.loads(n.payload)
                        except ValueError:
                            continue
                        for q in list(self._subs.get(str(d.get("b")), ())):
                            if q.qsize() < 200:
                                q.put_nowait({"table": d.get("t"), "conversation_id": d.get("c")})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("change hub connection lost; reconnecting")
                await asyncio.sleep(1)

    def subscribe(self, business_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subs[str(business_id)].add(q)
        return q

    def unsubscribe(self, business_id: uuid.UUID, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs[str(business_id)].discard(q)
