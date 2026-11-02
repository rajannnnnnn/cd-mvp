"""Scheduler role: outbox relay, lease reaper, queue gauges, daily summaries, nudges, billing cycle, alert delivery and
retention. One scheduler per COMPONENT (ADR 0025): `SCHEDULER_COMPONENT` picks the duties, so the web, gateway and agent
parts each keep their own timer and can be deployed, restarted and scaled on their own. Within a component exactly one
instance is active (a Postgres advisory lock per component); others stand by and take over if it dies (HLD: leader-elected).
Every action is idempotent (dedupe keys / state flags), so a brief double-run during failover is harmless."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from salesai.alerts import raise_alert
from salesai.config import ALL_QUEUES
from salesai.events.outbox import emit
from salesai.obs import QUEUE_DEAD, QUEUE_DEPTH, QUEUE_OLDEST_AGE
from salesai.queue.base import JobSpec
from salesai.runtime import Runtime

log = logging.getLogger("salesai.scheduler")
LEADER_LOCK = 7_283_461_002
# One lock per component, so the web, gateway and agent schedulers are each leader of their own work at the same time.
LEADER_LOCKS = {"all": LEADER_LOCK, "web": LEADER_LOCK + 1, "gateway": LEADER_LOCK + 2, "agent": LEADER_LOCK + 3}

# What each component's scheduler does. Moving events along (the relay) and expiring queue leases are shared plumbing that
# every scheduler does; the rest belongs to one component. `all` is the development combination and does everything.
_WEB = frozenset({"gauges", "alerts", "billing", "retention"})            # billing, trials, platform alerts, sign-in data
_AGENT = frozenset({"gauges", "daily_summaries", "nudges", "retention"})  # daily summaries, follow-ups, conversation data
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
    async def tick_daily_summaries(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        n = 0
        async with self.rt.db.system_tx() as c:
            rows = await (await c.execute("SELECT id, timezone, conversation_settings FROM businesses WHERE status IN ('active','onboarding') AND ai_enabled")).fetchall()
        for b in rows:
            local = now.astimezone(ZoneInfo(b["timezone"]))
            due = local.hour >= int((b["conversation_settings"] or {}).get("daily_summary_hour", 21))
            if due and await self.rt.queue.publish(JobSpec("owner.notifications", "daily_summary", {}, business_id=b["id"], ordering_key=f"biz:{b['id']}",
                                                           dedupe_key=f"summary:{b['id']}:{local.date().isoformat()}")):
                n += 1
        return n

    async def tick_nudges(self) -> int:
        """FR-SL-8: at most one in-window nudge after a customer who showed interest goes quiet."""
        async with self.rt.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT cv.id, cv.business_id, cv.version FROM conversations cv JOIN businesses b ON b.id = cv.business_id
                   WHERE b.ai_enabled AND cv.lead_stage IN ('interested','negotiating','ready_to_buy') AND NOT cv.selling_stopped
                     AND cv.nudge_sent_at IS NULL AND cv.last_outbound_at IS NOT NULL AND cv.last_inbound_at IS NOT NULL
                     AND cv.last_inbound_at < cv.last_outbound_at
                     AND cv.last_outbound_at < now() - make_interval(mins => COALESCE((b.conversation_settings->>'nudge_after_minutes')::int, 240))
                     AND cv.last_inbound_at > now() - interval '22 hours'
                     AND (cv.ai_paused_until IS NULL OR cv.ai_paused_until < now())
                   LIMIT 100""")).fetchall()
        n = 0
        for r in rows:
            async with self.rt.db.tenant(r["business_id"]) as c:
                upd = await c.execute("UPDATE conversations SET nudge_sent_at = now() WHERE id=%s AND nudge_sent_at IS NULL AND version=%s", (r["id"], r["version"]))
                if upd.rowcount:
                    await emit(c, "conversation.nudge_due", {"conversation_id": r["id"], "version": r["version"]}, business_id=r["business_id"],
                               ordering_key=f"conv:{r['id']}", entity_key=f"conversation:{r['id']}", entity_version=r["version"])
                    n += 1
        return n

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
        if "daily_summaries" in d:
            await self.tick_daily_summaries()
        if "nudges" in d:
            await self.tick_nudges()
        if "gauges" in d:
            await self.tick_gauges()
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
