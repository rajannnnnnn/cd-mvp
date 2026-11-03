"""The agent's clock (ADR 0038). `plan` looks at one conversation and makes its timers match what the chat needs now (rules in
`timer_rules`): set, move or cancel. Everything that changes a chat announces it (`conversation.state_changed`), so the plan is always
recomputed from the database's own state, never patched by the event that happened to arrive. `fire_due` is the agent's scheduler
waking the timers that are due: each is checked once more first, and then enters the normal turn path (turn detection's checks, INV-7,
INV-8, INV-10, the audit and the recovery sweep all apply unchanged)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from salesai.db import Conn, Database, jsonb
from salesai.events.outbox import emit
from salesai.modules.conversations.timer_rules import ConvState, desired, rhythm_of
from salesai.modules.delivery import next_open
from salesai.queue.base import Job
from salesai.rules import ai_block_reason

log = logging.getLogger("salesai.timers")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class TimerService:
    def __init__(self, db: Database):
        self.db = db

    async def handle(self, job: Job) -> None:
        """conversation.events:plan_timers: something changed in a chat; make its timers match."""
        bid = job.spec.business_id
        assert bid is not None
        await self.plan(bid, uuid.UUID(str(job.spec.payload["conversation_id"])))

    async def plan(self, bid: uuid.UUID, cid: uuid.UUID) -> dict[str, str]:
        async with self.db.tenant(bid) as c:
            return await self.plan_in(c, bid, cid)

    # ------------------------------------------------------------------ state -> desired timers -> database
    async def _state(self, c: Conn, cid: uuid.UUID) -> ConvState | None:
        conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s FOR UPDATE", (cid,))).fetchone()
        if conv is None:
            return None
        biz = await (await c.execute("SELECT * FROM businesses WHERE id=%s", (conv["business_id"],))).fetchone()
        cust = await (await c.execute("SELECT * FROM customers WHERE id=%s", (conv["customer_id"],))).fetchone()
        num = await (await c.execute("SELECT id, business_id, channel, phone_number_id, display_phone, verified_name, status, quality_rating, coexistence "
                                     "FROM whatsapp_numbers WHERE id=%s", (conv["whatsapp_number_id"],))).fetchone()
        if biz is None or cust is None:
            return None
        now = datetime.now(UTC)
        allowed = ai_block_reason(business=biz, conv=conv, customer=cust, number=num, now=now, ignore_pause=True) is None
        last = await (await c.execute(
            """SELECT direction, sender, status FROM messages WHERE conversation_id=%s AND NOT (direction = 'out' AND status IN ('cancelled', 'failed'))
                ORDER BY created_at DESC, id DESC LIMIT 1""", (cid,))).fetchone()
        queued = await (await c.execute("SELECT 1 FROM messages WHERE conversation_id=%s AND direction='out' AND sender='ai' AND status='queued' LIMIT 1", (cid,))).fetchone()
        waiting = await (await c.execute(
            """WITH out AS (SELECT COALESCE(max(created_at), %s) AS t FROM messages WHERE conversation_id=%s AND direction='out' AND status NOT IN ('cancelled', 'failed'))
               SELECT count(*) FILTER (WHERE NOT m.answered) AS unanswered, count(*) FILTER (WHERE m.answered) AS released
                 FROM messages m, out WHERE m.conversation_id=%s AND m.direction='in' AND m.sender='customer' AND m.created_at > out.t""", (EPOCH, cid, cid))).fetchone() or {}
        fired = await (await c.execute(
            "SELECT detail->>'after_inbound_at' AS after FROM agent_timers WHERE conversation_id=%s AND kind='follow_back' AND status='fired' ORDER BY fired_at DESC LIMIT 1",
            (cid,))).fetchone()
        lat = await (await c.execute(
            """SELECT extract(epoch FROM m.created_at - prev.created_at) AS s FROM messages m
                 JOIN LATERAL (SELECT o.created_at FROM messages o WHERE o.conversation_id = m.conversation_id AND o.direction = 'out'
                                  AND o.status IN ('sent', 'delivered', 'read') AND o.created_at < m.created_at ORDER BY o.created_at DESC LIMIT 1) prev ON true
                WHERE m.conversation_id=%s AND m.direction='in' AND m.sender='customer' ORDER BY m.created_at DESC LIMIT 10""", (cid,))).fetchall()
        speaker = None if last is None else ("customer" if last["direction"] == "in" else ("owner" if last["sender"] == "owner" else "ai"))
        settings = biz["conversation_settings"] or {}
        return ConvState(
            now=now, ai_allowed=allowed, paused_until=conv["ai_paused_until"], pause_reason=conv["ai_paused_reason"], last_inbound_at=conv["last_inbound_at"],
            last_outbound_at=conv["last_outbound_at"], last_speaker=speaker, waiting_unanswered=bool(waiting.get("unanswered")),
            waiting_released=bool(waiting.get("released")), queued_ai=queued is not None, lead_stage=conv["lead_stage"], selling_stopped=conv["selling_stopped"],
            follow_back_after=datetime.fromisoformat(fired["after"]) if fired and fired["after"] else None, rhythm_minutes=rhythm_of([float(r["s"]) for r in lat]),
            follow_back_minutes=int(settings.get("nudge_after_minutes", 240)), hours=biz["profile"] or {}, tz=biz["timezone"],
            hours_behavior=settings.get("business_hours_behavior", "reply_normally"))

    @staticmethod
    def _why_not(s: ConvState) -> str:
        if not s.ai_allowed:
            return "ai_not_allowed"
        if s.paused_until is not None and s.paused_until > s.now:
            return "paused"
        return {"customer": "customer_replied", "owner": "owner_replied"}.get(s.last_speaker or "", "no_longer_needed")

    async def plan_in(self, c: Conn, bid: uuid.UUID, cid: uuid.UUID) -> dict[str, str]:
        s = await self._state(c, cid)
        if s is None:
            return {}
        want = desired(s, next_open)
        result: dict[str, str] = {}
        rows = await (await c.execute("SELECT * FROM agent_timers WHERE conversation_id=%s AND status='pending'", (cid,))).fetchall()
        have = {r["kind"]: r for r in rows}
        for kind, p in want.items():
            detail = {"after_inbound_at": s.last_inbound_at.isoformat()} if kind == "follow_back" and s.last_inbound_at else {}
            cur = have.get(kind)
            if cur is None:
                await c.execute("INSERT INTO agent_timers (business_id, conversation_id, kind, due_at, reason, why, detail) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                (bid, cid, kind, p.due_at, p.reason, p.why, jsonb(detail)))
                result[kind] = "set"
            elif cur["due_at"] != p.due_at or cur["reason"] != p.reason:
                await c.execute("UPDATE agent_timers SET due_at=%s, reason=%s, why=%s, detail=%s, updated_at=now() WHERE id=%s",
                                (p.due_at, p.reason, p.why, jsonb(detail), cur["id"]))
                result[kind] = "moved"
        why_not = self._why_not(s)
        for kind, cur in have.items():
            if kind not in want:
                await c.execute("UPDATE agent_timers SET status='cancelled', cancelled_at=now(), cancel_reason=%s, updated_at=now() WHERE id=%s", (why_not, cur["id"]))
                result[kind] = "cancelled"
        return result

    # ------------------------------------------------------------------ the scheduler wakes what is due
    async def fire_due(self) -> int:
        async with self.db.system_tx() as c:
            due = await (await c.execute("SELECT id, business_id, conversation_id FROM agent_timers WHERE status='pending' AND due_at <= now() ORDER BY due_at LIMIT 100")).fetchall()
        fired = 0
        for d in due:
            try:
                fired += await self._fire(d["business_id"], d["id"], d["conversation_id"])
            except Exception:  # noqa: BLE001 - one bad timer must not stop the rest
                log.exception("timer %s failed to fire", d["id"])
        return fired

    async def _fire(self, bid: uuid.UUID, timer_id: uuid.UUID, cid: uuid.UUID) -> int:
        async with self.db.tenant(bid) as c:
            t = await (await c.execute("SELECT * FROM agent_timers WHERE id=%s AND status='pending' AND due_at <= now() FOR UPDATE SKIP LOCKED", (timer_id,))).fetchone()
            if t is None:
                return 0
            await self.plan_in(c, bid, cid)                      # still wanted, still due? the chat may have moved since the timer was set
            t = await (await c.execute("SELECT * FROM agent_timers WHERE id=%s", (timer_id,))).fetchone()
            if t is None or t["status"] != "pending" or t["due_at"] > datetime.now(UTC):
                return 0
            conv = await (await c.execute("SELECT version FROM conversations WHERE id=%s", (cid,))).fetchone()
            assert conv is not None
            await c.execute("UPDATE agent_timers SET status='fired', fired_at=now(), updated_at=now() WHERE id=%s", (timer_id,))
            if t["kind"] == "follow_back":
                await emit(c, "conversation.nudge_due", {"conversation_id": cid, "version": conv["version"]}, business_id=bid,
                           ordering_key=f"conv:{cid}", entity_key=f"conversation:{cid}", entity_version=conv["version"])
            else:
                await self._takeover(c, bid, cid, conv["version"])
        return 1

    async def _takeover(self, c: Conn, bid: uuid.UUID, cid: uuid.UUID, version: int) -> None:
        """Re-open what the customer wrote while the AI stood back, and hand it to turn detection like any new message."""
        rows = await (await c.execute(
            """UPDATE messages SET answered = false WHERE conversation_id=%s AND direction='in' AND sender='customer' AND answered
                  AND created_at > COALESCE((SELECT max(created_at) FROM messages WHERE conversation_id=%s AND direction='out' AND status NOT IN ('cancelled', 'failed')), %s)
               RETURNING id, created_at""", (cid, cid, EPOCH))).fetchall()
        if not rows:
            return
        newest: dict[str, Any] = max(rows, key=lambda r: r["created_at"])
        await emit(c, "conversation.eot_recheck", {"conversation_id": cid, "message_id": newest["id"], "version": version, "rechecks": 0, "cause": "takeover"},
                   business_id=bid, ordering_key=f"conv:{cid}", entity_key=f"conversation:{cid}", entity_version=version)

    # ------------------------------------------------------------------ the safety net re-plans chats whose timers may be missing
    async def plan_active(self, business_id: uuid.UUID | None = None, limit: int = 500) -> int:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT business_id, id FROM conversations WHERE (last_inbound_at > now() - interval '22 hours' OR ai_paused_until > now())
                      AND (%s::uuid IS NULL OR business_id = %s) ORDER BY updated_at DESC LIMIT %s""", (business_id, business_id, limit))).fetchall()
        for r in rows:
            await self.plan(r["business_id"], r["id"])
        return len(rows)
