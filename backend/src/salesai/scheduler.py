"""Scheduler role: outbox relay, lease reaper, queue gauges, daily summaries, nudges and retention. Exactly one
instance is active (Postgres advisory lock); others stand by and take over if it dies (HLD: leader-elected).
Every action is idempotent (dedupe keys / state flags), so a brief double-run during failover is harmless."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from salesai.config import ALL_QUEUES
from salesai.events.outbox import emit
from salesai.obs import QUEUE_DEAD, QUEUE_DEPTH, QUEUE_OLDEST_AGE
from salesai.queue.base import JobSpec
from salesai.runtime import Runtime

log = logging.getLogger("salesai.scheduler")
LEADER_LOCK = 7_283_461_002


class LeaderLock:
    """A dedicated connection holding a session-level advisory lock for as long as this process is leader."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.conn: psycopg.AsyncConnection | None = None

    async def try_acquire(self) -> bool:
        if self.conn is not None and not self.conn.closed:
            try:
                await self.conn.execute("SELECT 1")
                return True
            except Exception:  # noqa: BLE001
                self.conn = None
        conn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)
        row = await (await conn.execute("SELECT pg_try_advisory_lock(%s)", (LEADER_LOCK,))).fetchone()
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
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.leader = LeaderLock(rt.settings.system_database_url)

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

    async def tick_retention(self) -> dict[str, int]:
        """HLD retention: raw webhooks 30 days; finished jobs and stale auth artifacts shorter."""
        out: dict[str, int] = {}
        async with self.rt.db.system_tx() as c:
            out["webhook_events"] = (await c.execute("DELETE FROM webhook_events WHERE received_at < now() - interval '30 days'")).rowcount
            out["outbox"] = (await c.execute("DELETE FROM outbox WHERE published_at < now() - interval '7 days'")).rowcount
            out["otp_challenges"] = (await c.execute("DELETE FROM otp_challenges WHERE created_at < now() - interval '2 days'")).rowcount
            out["auth_sessions"] = (await c.execute("DELETE FROM auth_sessions WHERE (revoked_at IS NOT NULL AND revoked_at < now() - interval '30 days') OR expires_at < now() - interval '30 days'")).rowcount
        out["jobs"] = await self.rt.queue.purge_finished(timedelta(days=7))
        return out

    async def tick_gauges(self) -> None:
        for s in await self.rt.queue.stats(list(ALL_QUEUES)):
            QUEUE_DEPTH.labels(s.queue).set(s.pending)
            QUEUE_OLDEST_AGE.labels(s.queue).set(s.oldest_due_age_s)
            QUEUE_DEAD.labels(s.queue).set(s.dead)
            if s.dead:
                async with self.rt.db.system_tx() as c:
                    exists = await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='dead_letter' AND detail->>'queue'=%s AND resolved_at IS NULL", (s.queue,))).fetchone()
                    if not exists:
                        import json
                        await c.execute("INSERT INTO operator_alerts (severity, kind, message, detail) VALUES ('critical','dead_letter',%s,%s::jsonb)",
                                        (f"{s.dead} dead-lettered job(s) in {s.queue}", json.dumps({"queue": s.queue})))

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
                        await self.tick_daily_summaries()
                        await self.tick_nudges()
                        await self.tick_gauges()
                        if int(loop.time()) % 3600 < 60:
                            await self.tick_retention()
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
