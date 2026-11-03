"""Scheduler role: outbox relay, lease reaper, queue gauges, the agent's timers, billing cycle, alert delivery and
retention. One scheduler per COMPONENT (ADR 0025): `SCHEDULER_COMPONENT` picks the duties, so the web, gateway and agent
parts each keep their own timer and can be deployed, restarted and scaled on their own. Within a component exactly one
instance is active (a Postgres advisory lock per component); others stand by and take over if it dies (HLD: leader-elected).
Every action is idempotent (dedupe keys / state flags), so a brief double-run during failover is harmless."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

import psycopg

from salesai.alerts import raise_alert
from salesai.config import ALL_QUEUES
from salesai.obs import QUEUE_DEAD, QUEUE_DEPTH, QUEUE_OLDEST_AGE
from salesai.runtime import Runtime

log = logging.getLogger("salesai.scheduler")
LEADER_LOCK = 7_283_461_002
# One lock per component, so the web, gateway and agent schedulers are each leader of their own work at the same time.
LEADER_LOCKS = {"all": LEADER_LOCK, "web": LEADER_LOCK + 1, "gateway": LEADER_LOCK + 2, "agent": LEADER_LOCK + 3}

# What each component's scheduler does. Moving events along (the relay) and expiring queue leases are shared plumbing that
# every scheduler does; the rest belongs to one component. `all` is the development combination and does everything.
_WEB = frozenset({"gauges", "alerts", "billing", "retention"})            # billing, trials, platform alerts, sign-in data
_AGENT = frozenset({"gauges", "timers", "recovery", "retention"})  # the agent's clock (follow-back, take-over), the recovery sweep, conversation data
_GATEWAY = frozenset({"gauges", "retention"})                             # its raw-request log
DUTIES: dict[str, frozenset[str]] = {"web": _WEB, "agent": _AGENT, "gateway": _GATEWAY, "all": _WEB | _AGENT | _GATEWAY}


class LeaderLock:
    """A dedicated connection holding a session-level advisory lock for as long as this process is leader."""

    def __init__(self, dsn: str, lock_id: int = LEADER_LOCK):
        self.dsn, self.lock_id = dsn, lock_id
        self.conn: psycopg.AsyncConnection | None = None

    async def try_acquire(self) -> bool:
        if self.conn is not None and not self.conn.closed:
            try:
                await self.conn.execute("SELECT 1")
                return True
            except Exception:  # noqa: BLE001
                self.conn = None
        conn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)
        row = await (await conn.execute("SELECT pg_try_advisory_lock(%s)", (self.lock_id,))).fetchone()
        if row and row[0]:
            self.conn = conn
            return True
        await conn.close()
        return False

    async def release(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None


class Scheduler:
    def __init__(self, rt: Runtime, component: str | None = None):
        self.rt = rt
        self.component = component or rt.settings.scheduler_component
        self.duties = DUTIES[self.component]
        self.lock_id = LEADER_LOCKS[self.component]
        self.leader = LeaderLock(rt.settings.system_database_url, self.lock_id)

    # ------------------------------------------------------------------ ticks (each returns how many things it did)
    async def tick_timers(self) -> int:
        """Wake the conversation timers that are due: follow-backs and take-over checks (ADR 0038). Cheap, so it runs every pass."""
        return await self.rt.timers.fire_due()

    async def tick_retention(self, component: str | None = None) -> dict[str, int]:
        """Retention (HLD, ADR 0030). Each component tidies only the data it owns, so none needs rights over another's tables
        (ADR 0025): the web part sign-in and sign-up data, resolved alerts and ticket pictures; the gateway its raw-request log
        and simulator chats; the agent conversations, AI records and handled events. The shared plumbing (published events,
        finished jobs) is tidied by every component. Chat history and AI records go only when a period is configured."""
        comp = component or self.component
        everything = comp == "all"
        cfg = self.rt.settings
        out: dict[str, int] = {}
        async with self.rt.db.system_tx() as c:
            out["outbox"] = (await c.execute("DELETE FROM outbox WHERE published_at < now() - interval '7 days'")).rowcount
            if everything or comp == "gateway":
                out["webhook_events"] = (await c.execute(
                    "DELETE FROM webhook_events WHERE (signature_valid AND received_at < now() - make_interval(days => %s)) "
                    "OR (NOT signature_valid AND received_at < now() - make_interval(days => %s))",
                    (cfg.raw_webhook_retention_days, cfg.invalid_webhook_retention_days))).rowcount
                out["sim_messages"] = (await c.execute(
                    "DELETE FROM sim_messages WHERE created_at < now() - make_interval(days => %s)", (cfg.sim_message_retention_days,))).rowcount
            if everything or comp == "web":
                out["otp_challenges"] = (await c.execute("DELETE FROM otp_challenges WHERE created_at < now() - interval '2 days'")).rowcount
                # a gateway that was down never cleared these codes: expire them so the plain code lives one code lifetime at most
                out["otp_expired"] = (await c.execute(
                    "UPDATE otp_deliveries SET status='failed', error='expired', code=NULL WHERE status='pending' AND expires_at < now()")).rowcount
                out["otp_deliveries"] = (await c.execute("DELETE FROM otp_deliveries WHERE created_at < now() - interval '2 days'")).rowcount
                out["connect_expired"] = (await c.execute(       # Meta's one-time code is short-lived: never hold one past its use
                    "UPDATE number_connect_requests SET status='failed', code=NULL, error_status=422, error_code='code_exchange_failed', "
                    "error_message='Sign-up took too long. Please start again.', finished_at=now() "
                    "WHERE status='pending' AND created_at < now() - interval '30 minutes'")).rowcount
                out["number_connect_requests"] = (await c.execute("DELETE FROM number_connect_requests WHERE created_at < now() - interval '7 days'")).rowcount
                out["auth_sessions"] = (await c.execute("DELETE FROM auth_sessions WHERE (revoked_at IS NOT NULL AND revoked_at < now() - interval '30 days') OR expires_at < now() - interval '30 days'")).rowcount
                out["operator_alerts"] = (await c.execute(       # never an unresolved alert, however old
                    "DELETE FROM operator_alerts WHERE resolved_at IS NOT NULL AND resolved_at < now() - make_interval(days => %s)",
                    (cfg.resolved_alert_retention_days,))).rowcount
            if everything or comp == "agent":
                # handled events only: an event nobody has acted on yet is a customer message and is never discarded (NFR-3)
                out["channel_events"] = (await c.execute("DELETE FROM channel_events WHERE status='done' AND created_at < now() - interval '7 days'")).rowcount
            shops = [r["id"] for r in await (await c.execute("SELECT id FROM businesses")).fetchall()]
        out["jobs"] = await self.rt.queue.purge_finished(timedelta(days=7))
        # Shop data is deleted shop by shop through the shop's OWN row-secured role, so the platform role never holds
        # delete rights over chats, and row-level security cannot hide the rows meant to go (the session-4 bug class).
        if everything or comp == "web":
            out["support_attachments"] = 0
        if everything or comp == "agent":
            for k in ("messages", "owner_messages", "learning_observations", "turns"):
                out[k] = 0
        for bid in shops:
            async with self.rt.db.tenant(bid) as s:
                if "support_attachments" in out:
                    out["support_attachments"] += (await s.execute(
                        """DELETE FROM support_attachments a USING support_tickets k
                            WHERE k.id = a.ticket_id AND k.status = 'resolved' AND k.updated_at < now() - make_interval(days => %s)""",
                        (cfg.resolved_ticket_attachment_retention_days,))).rowcount
                if "messages" in out and cfg.chat_retention_days:          # 0 = keep while the shop is active (founder's policy decision)
                    days = (cfg.chat_retention_days,)
                    out["learning_observations"] += (await s.execute(
                        "DELETE FROM learning_observations WHERE created_at < now() - make_interval(days => %s)", days)).rowcount
                    out["owner_messages"] += (await s.execute(
                        "DELETE FROM owner_messages WHERE created_at < now() - make_interval(days => %s)", days)).rowcount
                    out["messages"] += (await s.execute(
                        "DELETE FROM messages WHERE created_at < now() - make_interval(days => %s)", days)).rowcount
                if "turns" in out and cfg.turn_retention_days:
                    out["turns"] += (await s.execute(
                        "DELETE FROM turns WHERE created_at < now() - make_interval(days => %s)", (cfg.turn_retention_days,))).rowcount
        return out

    async def tick_gauges(self) -> None:
        for s in await self.rt.queue.stats(list(ALL_QUEUES)):
            QUEUE_DEPTH.labels(s.queue).set(s.pending)
            QUEUE_OLDEST_AGE.labels(s.queue).set(s.oldest_due_age_s)
            QUEUE_DEAD.labels(s.queue).set(s.dead)
            if s.dead:
                async with self.rt.db.system_tx() as c:
                    # lost customer messages are Critical; any other queue stuck is High (ADR 0029). Only the first sighting is
                    # raised, so a long-dead queue is one alert, not a count that grows with the timer.
                    exists = await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='dead_letter' AND dedup_key=%s AND resolved_at IS NULL", (s.queue,))).fetchone()
                    if not exists:
                        await raise_alert(c, "critical" if s.queue in ("inbound.events", "conversation.events") else "high", "dead_letter",
                                          f"{s.dead} dead-lettered job(s) in {s.queue}", dedup_key=s.queue, detail={"queue": s.queue})

    async def tick_slow(self, *, tidy: bool) -> None:
        """The work done every half minute (and the hourly tidy-up when `tidy`), limited to this component's duties."""
        d = self.duties
        if "gauges" in d:
            await self.tick_gauges()
        if "timers" in d:
            await self.tick_timers()
        if "recovery" in d:
            await self.rt.reconciler.sweep()
        if "alerts" in d:
            await self.rt.alerts.dispatch()
        if "billing" in d:
            await self.rt.billing.run_cycle()
        if tidy and "retention" in d:
            await self.tick_retention()

    # ------------------------------------------------------------------ main loop
    async def run(self, stop: asyncio.Event) -> None:
        relay_task: asyncio.Task[None] | None = None
        slow_at = 0.0
        loop = asyncio.get_running_loop()
        try:
            while not stop.is_set():
                if not await self.leader.try_acquire():            # standby
                    if relay_task:
                        relay_task.cancel()
                        relay_task = None
                    await self._sleep(stop, 5)
                    continue
                if relay_task is None or relay_task.done():
                    relay_task = asyncio.create_task(self.rt.relay.run(stop, self.rt.settings.relay_poll_ms))
                try:
                    await self.rt.queue.reap_expired()
                    if "timers" in self.duties:
                        await self.tick_timers()                      # every pass: a follow-back should not wait for the half-minute tick
                    if loop.time() >= slow_at:
                        await self.tick_slow(tidy=int(loop.time()) % 3600 < 60)
                        slow_at = loop.time() + 30
                except Exception:  # noqa: BLE001
                    log.exception("scheduler tick failed")
                await self._sleep(stop, 2)
        finally:
            if relay_task:
                relay_task.cancel()
            await self.leader.release()

    @staticmethod
    async def _sleep(stop: asyncio.Event, s: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), s)
