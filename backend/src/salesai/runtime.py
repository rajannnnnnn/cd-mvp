"""Composition root: builds the object graph from settings. Every process role (API, ingress, worker,
scheduler) and every test builds the same graph; roles differ only in which loops they run."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from salesai.config import Settings, get_settings
from salesai.db import Database
from salesai.events.relay import OutboxRelay
from salesai.modules.agent import AgentService, Models
from salesai.modules.agent.llm.factory import make_llm
from salesai.modules.alerting import AlertRouter
from salesai.modules.alerting.email_sink import EmailSink, ses_deliver, smtp_deliver
from salesai.modules.alerting.router import WhatsAppSink
from salesai.modules.alerting.webhook_sink import WebhookSink
from salesai.modules.auth import AdminAuth, AuthService
from salesai.modules.billing import BillingService, ManualProvider, MetaUsage, TestGateway
from salesai.modules.boards import AgentAudit, Boards
from salesai.modules.channels import ChannelRegistry
from salesai.modules.channels.connect import NumberConnector
from salesai.modules.channels.otp import OtpSender
from salesai.modules.channels.owner_send import OwnerSender
from salesai.modules.channels.router import WebhookRouter
from salesai.modules.channels.simulator import SimulatorNetwork
from salesai.modules.conversations import ConversationIntake, HeuristicEOT, TurnWorker
from salesai.modules.conversations.recovery import Reconciler
from salesai.modules.conversations.timers import TimerService
from salesai.modules.delivery.sender import OutboundSender
from salesai.modules.mail import MailDispatcher, founder_alert_html, founder_digest_html
from salesai.modules.notifications import OwnerLoop
from salesai.modules.pricing import PricingService
from salesai.modules.tenants import TokenVault
from salesai.queue.base import Queue
from salesai.queue.postgres import PostgresQueue
from salesai.queue.worker import HandlerRegistry, StalenessResolver, WorkerRunner

log = logging.getLogger("salesai.runtime")


def _client_mail_transport(settings: Settings) -> Any:
    """Mail to shop owners leaves the same way as the founder's: SES or SMTP, chosen by EMAIL_TRANSPORT."""
    if settings.email_transport == "ses":
        return ses_deliver(settings.ses_region, settings.ses_access_key_id, settings.ses_secret_access_key)
    return smtp_deliver(settings.smtp_host, settings.smtp_port, settings.smtp_security, settings.smtp_username, settings.smtp_password)


def build_alert_router(db: Database, channels: ChannelRegistry, settings: Settings) -> AlertRouter:
    sinks: list[Any] = []
    if settings.alert_emails:                  # the founder's inbox first (ADR 0029); these addresses are configuration, nothing else
        deliver = (ses_deliver(settings.ses_region, settings.ses_access_key_id, settings.ses_secret_access_key)
                   if settings.email_transport == "ses" else None)
        sinks.append(EmailSink(settings.smtp_host, settings.smtp_port, sender=settings.alert_email_from, to=settings.alert_emails,
                               security=settings.smtp_security, username=settings.smtp_username, password=settings.smtp_password, deliver=deliver,
                               html_alert=founder_alert_html, html_digest=founder_digest_html))
    if settings.alert_webhook_url:
        sinks.append(WebhookSink(settings.alert_webhook_url))
    if settings.alert_numbers:
        sinks.append(WhatsAppSink(channels, settings.alert_numbers))
    return AlertRouter(db, sinks, min_severity=settings.alert_min_severity, reminder_minutes=settings.alert_reminder_minutes,
                       high_cooldown_minutes=settings.alert_high_cooldown_minutes, medium_rollup_minutes=settings.alert_medium_rollup_minutes,
                       low_digest_minutes=settings.alert_low_digest_minutes)


class Runtime:
    def __init__(self, settings: Settings, db: Database, queue: Queue):
        self.settings, self.db, self.queue = settings, db, queue
        self.vault = TokenVault(db, settings.master_key_bytes if settings.master_key else None)   # None: this role never opens tokens (ADR 0020)
        self.channels = ChannelRegistry(db, self.vault, graph_base=settings.meta_graph_base,
                                        graph_version=settings.meta_graph_version,
                                        simulator_enabled=settings.simulator_enabled, platform_channel=settings.otp_channel,
                                        platform_phone_number_id=settings.platform_sender_phone_number_id,
                                        platform_token=settings.platform_wa_access_token)
        self.relay = OutboxRelay(db, queue)
        self.router = WebhookRouter(db)                         # the gateway reads Meta's webhook
        self.intake = ConversationIntake(db, queue)             # the agent acts on what the gateway handed over
        self.registry = HandlerRegistry()
        self.staleness = StalenessResolver()
        self.pricing = PricingService(db)
        self.llm = make_llm(settings)
        self.agent = AgentService(db, self.llm, self.pricing,
                                  Models(settings.llm_model_planner, settings.llm_model_writer, settings.llm_model_check,
                                         settings.llm_effort_planner, settings.llm_effort_writer, settings.llm_effort_check),
                                  llm_checks=settings.llm_stage_provider("check") != "local",
                                  skip_check_when_low_risk=settings.tiering_skip_check_when_low_risk)
        self.turns = TurnWorker(db, self.agent, HeuristicEOT(), max_turns_per_10min=settings.max_ai_turns_per_conversation_10min,
                               alert_turns_per_10min=settings.alert_ai_turns_per_conversation_10min)
        self.sender = OutboundSender(db, self.channels)
        self.otp = OtpSender(db, self.channels)
        self.connector = NumberConnector(db, self.vault, settings)
        self.owner_sender = OwnerSender(db, self.channels)
        self.owner = OwnerLoop(db, self.llm, settings.llm_model_check)
        self.auth = AuthService(db, settings, pump=self._pump)
        self.admin_auth = AdminAuth(db, settings, self.auth, deliver=_client_mail_transport(settings))     # the founder's console sign-in (ADR 0028)
        self.sim = SimulatorNetwork(db, settings.meta_app_secret)
        self.alerts = build_alert_router(db, self.channels, settings)
        self.mail = MailDispatcher(db, deliver=_client_mail_transport(settings), sender_for=settings.client_sender, bcc_for=settings.client_bcc,
                                   site_url=settings.owner_site_url, per_minute=settings.client_email_per_minute, enabled=settings.client_mail_enabled)
        self.billing = BillingService(db, ManualProvider(settings.payment_instructions) if settings.payment_provider == "manual" else TestGateway(), seller={"name": settings.invoice_seller_name, "address": settings.invoice_seller_address, "gstin": settings.invoice_seller_gstin})
        self.meta_usage = MetaUsage(db, rates_inr=settings.meta_rates, free_service_per_number=settings.meta_free_service_per_number)
        self.timers = TimerService(db)                           # the agent's own clock: follow-back and take-over (ADR 0038)
        self.reconciler = Reconciler(db, timers=self.timers, stuck_after_s=settings.recovery_stuck_after_s, retry_after_s=settings.recovery_retry_after_s,
                                     max_attempts=settings.recovery_max_attempts, queued_message_s=settings.recovery_queued_reply_s,
                                     mailbox_s=settings.recovery_mailbox_s)      # the agent's safety net under the queue (ADR 0035)
        self.agent_audit = AgentAudit(db)                       # the founder's view of every AI run (ADR 0037)
        self.boards = Boards(db, queue, self.meta_usage)       # the founder's four boards (ADR 0028)
        self.pump: Callable[[], Awaitable[None]] = self._wait_a_moment
        self.hub: Any = None
        self.extra: dict[str, Any] = {}
        self._wire()

    @classmethod
    async def create(cls, settings: Settings | None = None, queue_factory: Callable[[Database, Settings], Queue] | None = None) -> Runtime:
        s = settings or get_settings()
        db = Database(s.database_url, s.pricing_database_url or None, s.system_database_url, s.db_pool_max, s.console_database_url or None)
        await db.open()
        queue = queue_factory(db, s) if queue_factory else make_queue(db, s)
        return cls(s, db, queue)

    async def close(self) -> None:
        await self.channels.cloud.aclose()
        await self.db.close()

    async def _wait_a_moment(self) -> None:
        await asyncio.sleep(0.1)

    async def _pump(self) -> None:
        """What the web side does between looks at a request the gateway is answering. In production the gateway is another
        process and this just waits; tests replace `pump` to run the real send queue inline, as World.drain does."""
        await self.pump()

    def _wire(self) -> None:
        self.registry.add("inbound.events", "route_webhook", self.router.route_webhook)                      # gateway: read Meta
        self.registry.add("conversation.events", "channel_event", self.intake.handle)                       # agent: act on it
        self.registry.add("conversation.events", "plan_timers", self.timers.handle)
        self.registry.add("conversation.turns", "eot_check", self.turns.eot_check)
        self.registry.add("conversation.turns", "nudge", self.turns.nudge)
        self.registry.add("outbound.actions", "execute", self.sender.execute)
        self.registry.add("outbound.actions", "send_otp", self.otp.send_otp)            # the gateway sends what the web asked for
        self.registry.add("outbound.actions", "connect_number", self.connector.connect)  # ...and completes number sign-ups for it
        self.registry.add("outbound.actions", "send_owner_message", self.owner_sender.send)   # ...and sends the agent's owner alerts
        self.registry.add("owner.notifications", "notify", self.owner.notify)
        self.registry.add("owner.notifications", "owner_chat", self.owner.owner_chat)
        self.registry.add("platform.events", "account_update", self.owner.account_update)
        self.registry.add("platform.events", "number_status", self.owner.number_status)

        async def conv_version(key: str) -> int | None:
            async with self.db.system_tx() as c:
                r = await (await c.execute("SELECT version FROM conversations WHERE id=%s", (key.split(":", 1)[1],))).fetchone()
            return r["version"] if r else None

        self.staleness.register("conversation", conv_version)

    def worker(self, queues: list[str] | None = None, **kw: Any) -> WorkerRunner:
        return WorkerRunner(self.queue, self.registry, queues or self.settings.queues, staleness=self.staleness,
                            concurrency=kw.pop("concurrency", self.settings.worker_concurrency),
                            lease_s=kw.pop("lease_s", self.settings.worker_lease_s),
                            shutdown_grace_s=kw.pop("shutdown_grace_s", self.settings.worker_shutdown_grace_s), **kw)


def make_queue(db: Database, s: Settings) -> Queue:
    if s.queue_backend == "postgres":
        return PostgresQueue(db)
    from salesai.queue.redis_backend import RedisQueue  # noqa: PLC0415
    return RedisQueue(s.redis_url)
