"""Turn worker: end-of-turn check loop, agent invocation, nudges. Runs on conversation.turns, serialized per
conversation by the queue's ordering key. Every wait is a DELAYED JOB, never a sleep inside a worker."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from salesai.db import Database, required
from salesai.events.outbox import emit
from salesai.modules.agent import AgentService
from salesai.modules.conversations.eot import EndOfTurnPredictor, EotInput
from salesai.modules.handoffs import create_handoff
from salesai.obs import FLOOD_GUARD, bind
from salesai.queue.base import Job
from salesai.rules import ai_block_reason

log = logging.getLogger("salesai.turns")


class TurnWorker:
    def __init__(self, db: Database, agent: AgentService, eot: EndOfTurnPredictor, *, max_turns_per_10min: int = 30):
        self.db, self.agent, self.eot = db, agent, eot
        self.max_turns_per_10min = max_turns_per_10min

    async def eot_check(self, job: Job) -> None:
        p = job.spec.payload
        bid, cid, version = job.spec.business_id, uuid.UUID(p["conversation_id"]), int(p["version"])
        assert bid is not None
        rechecks = int(p.get("rechecks", 0))
        try:
            await self._eot(bid, cid, version, rechecks)
        except Exception:
            if job.attempts >= job.spec.max_attempts:       # INV-12: last attempt failed -> handoff, not silence
                await self._give_up(bid, cid, version)
            raise

    async def _eot(self, bid: uuid.UUID, cid: uuid.UUID, version: int, rechecks: int) -> None:
        now = datetime.now(UTC)
        with bind(business_id=bid, conversation_id=cid):
            async with self.db.tenant(bid) as c:
                conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s", (cid,))).fetchone()
                if conv is None or conv["version"] != version:
                    return                                    # INV-7: planned for an older state
                msgs = await (await c.execute(
                    """SELECT id, kind, body, wa_timestamp, created_at FROM messages
                       WHERE conversation_id=%s AND direction='in' AND NOT answered ORDER BY created_at, id""", (cid,))).fetchall()
                if not msgs:
                    await c.execute("UPDATE conversations SET state='idle' WHERE id=%s AND state='listening'", (cid,))
                    return
                biz = required(await (await c.execute("SELECT * FROM businesses WHERE id=%s", (bid,))).fetchone(), "biz")
                cust = required(await (await c.execute("SELECT * FROM customers WHERE id=%s", (conv["customer_id"],))).fetchone(), "cust")
                num = required(await (await c.execute("SELECT * FROM whatsapp_numbers WHERE id=%s", (conv["whatsapp_number_id"],))).fetchone(), "num")
                gaps = [r["gap_ms"] for r in await (await c.execute(
                    "SELECT gap_ms FROM messages WHERE conversation_id=%s AND direction='in' AND gap_ms IS NOT NULL ORDER BY created_at DESC LIMIT 20", (cid,))).fetchall()]
            block = ai_block_reason(business=biz, conv=conv, customer=cust, number=num, now=now)
            if block:                                         # INV-10: recorded, never answered
                await self._release(bid, cid, version)
                log.info("AI blocked (%s): messages recorded, not answered", block)
                return
            settings = biz["conversation_settings"] or {}
            decision = await self.eot.decide(EotInput(
                texts=[m["body"] or "" for m in msgs], kinds=[m["kind"] for m in msgs],
                last_message_at=msgs[-1]["created_at"], first_unanswered_at=msgs[0]["created_at"], now=now, gaps_ms=gaps, settings=settings))
            if not decision.ready:
                async with self.db.tenant(bid) as c:
                    await emit(c, "conversation.eot_recheck",
                               {"conversation_id": cid, "message_id": msgs[-1]["id"], "version": version, "rechecks": rechecks + 1},
                               business_id=bid, ordering_key=f"conv:{cid}", entity_key=f"conversation:{cid}", entity_version=version,
                               run_at=now + timedelta(milliseconds=decision.wait_ms))
                return
            if await self._flooded(bid, cid):                  # abuse guard: record the messages, stop spending on replies
                FLOOD_GUARD.inc()
                await self._release(bid, cid, version)
                log.warning("conversation exceeded its AI turn budget; messages recorded, not answered")
                return
            signals = {"eot": {"reason": decision.reason, "rechecks": rechecks,
                               "waited_ms": int((now - msgs[0]["created_at"]).total_seconds() * 1000), "messages": len(msgs)}}
            outcome = await self.agent.run_turn(bid, cid, version, signals=signals)
            log.info("turn %s (%s)", outcome.status, outcome.intent or outcome.reason)

    async def nudge(self, job: Job) -> None:
        """FR-SL-8: at most one in-window nudge after the customer goes quiet; never when selling has stopped."""
        p = job.spec.payload
        bid, cid, version = job.spec.business_id, uuid.UUID(p["conversation_id"]), int(p["version"])
        assert bid is not None
        async with self.db.tenant(bid) as c:
            conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s", (cid,))).fetchone()
            if conv is None or conv["version"] != version or conv["selling_stopped"] or conv["lead_stage"] in ("won", "lost", "new"):
                return          # the scheduler already marked nudge_sent_at when it requested this (at most one nudge)
        await self.agent.run_turn(bid, cid, version, trigger="nudge", signals={"nudge": True})

    async def _flooded(self, bid: uuid.UUID, cid: uuid.UUID) -> bool:
        async with self.db.tenant(bid) as c:
            n = (await (await c.execute(
                "SELECT count(*) AS n FROM turns WHERE conversation_id=%s AND created_at > now() - interval '10 minutes'", (cid,))).fetchone())["n"]
        return int(n) >= self.max_turns_per_10min

    async def _release(self, bid: uuid.UUID, cid: uuid.UUID, version: int) -> None:
        async with self.db.tenant(bid) as c:
            await c.execute("UPDATE messages SET answered=true WHERE conversation_id=%s AND direction='in' AND NOT answered", (cid,))
            await c.execute("UPDATE conversations SET state='idle' WHERE id=%s AND version=%s", (cid, version))

    async def _give_up(self, bid: uuid.UUID, cid: uuid.UUID, version: int) -> None:
        async with self.db.tenant(bid) as c:
            await create_handoff(c, bid, cid, "system_failure", "The AI could not process this conversation after repeated failures", pause_minutes=120)
            await c.execute("UPDATE messages SET answered=true WHERE conversation_id=%s AND direction='in' AND NOT answered", (cid,))
            await c.execute("UPDATE conversations SET state='idle' WHERE id=%s", (cid,))
        async with self.db.system_tx() as s:
            await s.execute("INSERT INTO operator_alerts (business_id, severity, kind, message, detail) VALUES (%s,'critical','turn_failed',%s,%s::jsonb)",
                            (bid, "AI turn failed repeatedly; conversation handed off", json.dumps({"conversation_id": str(cid)})))
