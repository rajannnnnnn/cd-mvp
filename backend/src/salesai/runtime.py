"""Composition root: builds the object graph from settings. Every process role (API, ingress, worker,
scheduler) and every test builds the same graph; roles differ only in which loops they run."""
from __future__ import annotations

import logging
from typing import Any

from salesai.config import Settings, get_settings
from salesai.db import Database
from salesai.events.relay import OutboxRelay
from salesai.modules.channels import ChannelRegistry
from salesai.modules.conversations.inbound import InboundRouter
from salesai.modules.tenants import TokenVault
from salesai.queue.base import Queue
from salesai.queue.postgres import PostgresQueue
from salesai.queue.worker import HandlerRegistry, StalenessResolver, WorkerRunner

log = logging.getLogger("salesai.runtime")


class Runtime:
    def __init__(self, settings: Settings, db: Database, queue: Queue):
        self.settings, self.db, self.queue = settings, db, queue
        self.vault = TokenVault(db, settings.master_key_bytes)
        self.channels = ChannelRegistry(db, self.vault, graph_base=settings.meta_graph_base,
                                        graph_version=settings.meta_graph_version,
                                        simulator_enabled=settings.simulator_enabled)
        self.relay = OutboxRelay(db, queue)
        self.router = InboundRouter(db, queue)
        self.registry = HandlerRegistry()
        self.staleness = StalenessResolver()
        self.extra: dict[str, Any] = {}
        self._wire()

    @classmethod
    async def create(cls, settings: Settings | None = None, queue_factory=None) -> Runtime:  # noqa: ANN001
        s = settings or get_settings()
        db = Database(s.database_url, s.pricing_database_url, s.system_database_url, s.db_pool_max)
        await db.open()
        queue = queue_factory(db, s) if queue_factory else make_queue(db, s)
        return cls(s, db, queue)

    async def close(self) -> None:
        await self.channels.cloud.aclose()
        await self.db.close()

    def _wire(self) -> None:
        self.registry.add("inbound.events", "route_webhook", self.router.route_webhook)

        async def conv_version(key: str) -> int | None:
            async with self.db.system_tx() as c:
                r = await (await c.execute("SELECT version FROM conversations WHERE id=%s", (key.split(":", 1)[1],))).fetchone()
            return r["version"] if r else None

        self.staleness.register("conversation", conv_version)

    def worker(self, queues: list[str] | None = None, **kw: Any) -> WorkerRunner:
        return WorkerRunner(self.queue, self.registry, queues or self.settings.queues, staleness=self.staleness,
                            concurrency=kw.pop("concurrency", self.settings.worker_concurrency), **kw)


def make_queue(db: Database, s: Settings) -> Queue:
    if s.queue_backend == "postgres":
        return PostgresQueue(db)
    from salesai.queue.redis_backend import RedisQueue  # noqa: PLC0415
    return RedisQueue(s.redis_url)
