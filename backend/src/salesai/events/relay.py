"""Outbox relay: publishes outbox rows to the queue (one active instance is enough, more are safe:
rows are claimed with SKIP LOCKED and job dedupe keys make republishing idempotent)."""
from __future__ import annotations

import asyncio
import contextlib
import logging

from salesai.db import Database
from salesai.events.catalog import CATALOG
from salesai.queue.base import JobSpec, Queue

log = logging.getLogger("salesai.relay")


class OutboxRelay:
    def __init__(self, db: Database, queue: Queue, batch: int = 100):
        self.db, self.queue, self.batch = db, queue, batch

    async def publish_batch(self) -> int:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT * FROM outbox WHERE published_at IS NULL
                   ORDER BY seq LIMIT %s FOR UPDATE SKIP LOCKED""", (self.batch,))).fetchall()
            for r in rows:
                spec = CATALOG.get(r["event_type"])
                if spec is None:
                    log.error("unknown event type in outbox: %s", r["event_type"])
                else:
                    envelope = {"id": str(r["id"]), "type": r["event_type"], "schema_version": r["schema_version"],
                                "occurred_at": r["occurred_at"].isoformat()}
                    for sub in spec.subscribers:
                        await self.queue.publish(JobSpec(
                            queue=sub.queue, kind=sub.kind,
                            payload={**r["payload"], "_event": envelope},
                            business_id=r["business_id"], ordering_key=r["ordering_key"],
                            entity_key=r["entity_key"], entity_version=r["entity_version"],
                            run_at=r["run_at"], dedupe_key=f"{r['id']}:{sub.queue}:{sub.kind}"))
                await c.execute("UPDATE outbox SET published_at = now() WHERE id = %s", (r["id"],))
        return len(rows)

    async def run(self, stop: asyncio.Event, poll_ms: int = 100) -> None:
        while not stop.is_set():
            try:
                n = await self.publish_batch()
            except Exception:  # noqa: BLE001
                log.exception("relay batch failed")
                n = 0
            if n < self.batch:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), poll_ms / 1000)
