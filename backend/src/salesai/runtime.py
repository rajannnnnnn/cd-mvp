"""Composition root: builds the object graph from settings. Every process role (API, ingress, worker,
scheduler) and every test builds the same graph; roles differ only in which loops they run."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from salesai.config import Settings, get_settings
from salesai.db import Database
from salesai.events.relay import OutboxRelay
from salesai.modules.agent import AgentService, Models
from salesai.modules.agent.llm.factory import make_llm
from salesai.modules.alerting import AlertRouter
from salesai.modules.alerting.router import WhatsAppSink
from salesai.modules.alerting.webhook_sink import WebhookSink
from salesai.modules.auth import AuthService
from salesai.modules.billing import BillingService, ManualProvider, TestGateway
from salesai.modules.channels import ChannelRegistry
from salesai.modules.channels.simulator import SimulatorNetwork
from salesai.modules.conversations import HeuristicEOT, InboundRouter, TurnWorker
from salesai.modules.delivery.sender import OutboundSender
from salesai.modules.notifications import OwnerLoop
from salesai.modules.pricing import PricingService
from salesai.modules.tenants import TokenVault
from salesai.queue.base import Queue
from salesai.queue.postgres import PostgresQueue
from salesai.queue.worker import HandlerRegistry, StalenessResolver, WorkerRunner

log = logging.getLogger("salesai.runtime")


def build_alert_router(db: Database, channels: ChannelRegistry, settings: Settings) -> AlertRouter:
    sinks: list[Any] = []
    if settings.alert_webhook_url:
        sinks.append(WebhookSink(settings.alert_webhook_url))
    if settings.alert_numbers:
        sinks.append(WhatsAppSink(channels, settings.alert_numbers))
    return AlertRouter(db, sinks, min_severity=settings.alert_min_severity, reminder_minutes=settings.alert_reminder_minutes)


class Runtime:
    def __init__(self, settings: Settings, db: Database, queue: Queue):
        self.settings, self.db, self.queue = settings, db, queue
        self.vault = TokenVault(db, settings.master_key_bytes)
        self.channels = ChannelRegistry(db, self.vault, graph_base=settings.meta_graph_base,
                                        graph_version=settings.meta_graph_version,
                                        simulator_enabled=settings.simulator_enabled, platform_channel=settings.otp_channel,
                                        platform_phone_number_id=settings.platform_sender_phone_number_id,
                                        platform_token=settings.platform_wa_access_token)
        self.relay = OutboxRelay(db, queue)
        self.router = InboundRouter(db, queue)
        self.registry = HandlerRegistry()
        self.staleness = StalenessResolver()
        self.pricing = PricingService(db)
        self.llm = make_llm(settings)
        self.agent = AgentService(db, self.llm, self.pricing,
                                  Models(settings.llm_model_planner, settings.llm_model_writer, settings.llm_model_check),
                                  llm_checks=settings.llm_provider != "local")
        self.turns = TurnWorker(db, self.agent, HeuristicEOT(), max_turns_per_10min=settings.max_ai_turns_per_conversation_10min)
        self.sender = OutboundSender(db, self.channels)
        self.owner = OwnerLoop(db, self.channels, self.llm, settings.llm_model_check)
        self.auth = AuthService(db, self.channels, settings)
        self.sim = SimulatorNetwork(db, settings.meta_app_secret)
        self.alerts = build_alert_router(db, self.channels, settings)
        self.billing = BillingService(db, ManualProvider(settings.payment_instructions) if settings.payment_provider == "manual" else TestGateway(), seller={"name": settings.invoice_seller_name, "address": settings.invoice_seller_address, "gstin": settings.invoice_seller_gstin})
        self.hub: Any = None
        self.extra: dict[str, Any] = {}
        self._wire()

    @classmethod
    async def create(cls, settings: Settings | None = None, queue_factory: Callable[[Database, Settings], Queue] | None = None) -> Runtime:
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
        self.registry.add("conversation.turns", "eot_check", self.turns.eot_check)
        self.registry.add("conversation.turns", "nudge", self.turns.nudge)
        self.registry.add("outbound.actions", "execute", self.sender.execute)
        self.registry.add("owner.notifications", "notify", self.owner.notify)
        self.registry.add("owner.notifications", "owner_chat", self.owner.owner_chat)
        self.registry.add("owner.notifications", "daily_summary", self.owner.daily_summary)
        self.registry.add("platform.events", "account_update", self.owner.account_update)
        self.registry.add("platform.events", "number_status", self.owner.number_status)

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
