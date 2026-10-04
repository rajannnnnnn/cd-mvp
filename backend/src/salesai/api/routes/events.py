from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from salesai.api.deps import RT, Tenant

router = APIRouter(tags=["live"])


@router.get("/events", summary="Server-sent events: tenant change notifications (messages, conversations, handoffs, deals, gaps)")
async def events(request: Request, rt: RT, p: Tenant) -> EventSourceResponse:
    hub = rt.hub
    q = hub.subscribe(p.business_id)  # type: ignore[arg-type]

    async def gen():  # noqa: ANN202
        try:
            yield {"event": "ready", "data": "{}"}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    first = await asyncio.wait_for(q.get(), timeout=15)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                batch = {(first["table"], first["conversation_id"])}
                await asyncio.sleep(0.15)            # coalesce bursts
                while not q.empty():
                    e = q.get_nowait()
                    batch.add((e["table"], e["conversation_id"]))
                yield {"event": "change", "data": json.dumps([{"table": t, "conversation_id": c} for t, c in sorted(batch, key=str)])}
        finally:
            hub.unsubscribe(p.business_id, q)  # type: ignore[arg-type]

    return EventSourceResponse(gen())
