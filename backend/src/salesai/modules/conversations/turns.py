"""Turn worker: end-of-turn check loop, agent invocation, nudges. Runs on conversation.turns, serialized per
conversation by the queue's ordering key. Every wait is a DELAYED JOB, never a sleep inside a worker."""
from __future__ import annotations

import logging
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from salesai.alerts import raise_alert
from salesai.db import Database, required
from salesai.events.outbox import emit
from salesai.logsummary import turn_line
from salesai.modules.agent import AgentService
from salesai.modules.conversations.eot import EndOfTurnPredictor, EotInput
from salesai.modules.conversations.runs import finish_run, finish_with, record_instant, start_run
from salesai.modules.delivery import sample
from salesai.modules.handoffs import create_handoff
from salesai.obs import FLOOD_GUARD, bind
from salesai.queue.base import Job
from salesai.rules import ai_block_reason

log = logging.getLogger("salesai.turns")


class TurnWorker:
    def __init__(self, db: Database, agent: AgentService, eot: EndOfTurnPredictor, *, max_turns_per_10min: int = 0, alert_turns_per_10min: int = 60):
        self.db, self.agent, self.eot = db, agent, eot
        self.max_turns_per_10min = max_turns_per_10min          # 0 = no limit: a customer is never refused a reply (founder, 2026-11-23)
        self.alert_turns_per_10min = alert_turns_per_10min      # a conversation this active is told to the founder, never blocked

    async def eot_check(self, job: Job) -> None:
        p = job.spec.payload
        bid, cid, version = job.spec.business_id, uuid.UUID(p["conversation_id"]), int(p["version"])
        assert bid is not None
        rechecks = int(p.get("rechecks", 0))
        try:
            await self._eot(bid, cid, version, rechecks, p.get("cause"))
        except Exception:
            if job.attempts >= job.spec.max_attempts:       # INV-12: last attempt failed -> handoff, not silence
                await self._give_up(bid, cid, version)
            raise

    async def _eot(self, bid: uuid.UUID, cid: uuid.UUID, version: int, rechecks: int, cause: str | None = None) -> None:
        now = datetime.now(UTC)
        with bind(business_id=bid, conversation_id=cid):
            async with self.db.tenant(bid) as c:
                conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s", (cid,))).fetchone()
                if conv is None or conv["version"] != version:
                    return                                    # INV-7: planned for an older state
                msgs = await (await c.execute(
                    """SELECT id, kind, body, wa_message_id, wa_timestamp, created_at FROM messages
                       WHERE conversation_id=%s AND direction='in' AND NOT answered ORDER BY created_at, id""", (cid,))).fetchall()
                if not msgs:
                    await c.execute("UPDATE conversations SET state='idle' WHERE id=%s AND state='listening'", (cid,))
                    return
                biz = required(await (await c.execute("SELECT * FROM businesses WHERE id=%s", (bid,))).fetchone(), "biz")
                cust = required(await (await c.execute("SELECT * FROM customers WHERE id=%s", (conv["customer_id"],))).fetchone(), "cust")
                num = required(await (await c.execute(            # no access token here: only the send part reads it (INV-11, ADR 0020)
                    "SELECT id, business_id, channel, phone_number_id, display_phone, verified_name, status, quality_rating, coexistence "
                    "FROM whatsapp_numbers WHERE id=%s", (conv["whatsapp_number_id"],))).fetchone(), "num")
                gaps = [r["gap_ms"] for r in await (await c.execute(
                    "SELECT gap_ms FROM messages WHERE conversation_id=%s AND direction='in' AND gap_ms IS NOT NULL ORDER BY created_at DESC LIMIT 20", (cid,))).fetchall()]
                last_ai = await (await c.execute(
                    "SELECT body FROM messages WHERE conversation_id=%s AND direction='out' AND sender='ai' AND kind='text' AND status <> 'cancelled' ORDER BY created_at DESC, id DESC LIMIT 1",
                    (cid,))).fetchone()
                ai_asked = bool(last_ai and (last_ai["body"] or "").rstrip().endswith(("?", "？")))     # a short reply to our question is an answer, not a fragment
            block = ai_block_reason(business=biz, conv=conv, customer=cust, number=num, now=now)
            if block:                                         # INV-10: recorded, never answered
                await self._release(bid, cid, version)
                log.info("AI blocked (%s): messages recorded, not answered", block)
                await record_instant(self.db, bid, cid, "turn_detection", {"messages": len(msgs)}, "blocked", block, msgs[0]["created_at"])
                return
            settings = biz["conversation_settings"] or {}
            decision = await self.eot.decide(EotInput(
                texts=[m["body"] or "" for m in msgs], kinds=[m["kind"] for m in msgs],
                last_message_at=msgs[-1]["created_at"], first_unanswered_at=msgs[0]["created_at"], now=now, gaps_ms=gaps, settings=settings,
                ai_asked_question=ai_asked, message_times=[m["created_at"] for m in msgs]))
            if not decision.ready:
                async with self.db.tenant(bid) as c:
                    await emit(c, "conversation.eot_recheck",
                               {"conversation_id": cid, "message_id": msgs[-1]["id"], "version": version, "rechecks": rechecks + 1, "cause": cause},
                               business_id=bid, ordering_key=f"conv:{cid}", entity_key=f"conversation:{cid}", entity_version=version,
                               run_at=now + timedelta(milliseconds=decision.wait_ms))
                return
            turns_10min = await self._recent_turns(bid, cid)
            if self.max_turns_per_10min and turns_10min >= self.max_turns_per_10min:      # only when someone set a limit on purpose
                FLOOD_GUARD.inc()
                await self._release(bid, cid, version)
                log.warning("conversation exceeded its AI turn budget; messages recorded, not answered")
                await record_instant(self.db, bid, cid, "turn_detection", {"messages": len(msgs)}, "blocked", "flood_guard", msgs[0]["created_at"])
                return
            if self.alert_turns_per_10min and turns_10min >= self.alert_turns_per_10min:
                await self._tell_founder_unusually_active(bid, cid, turns_10min)             # visible, never blocking
            signals: dict[str, Any] = {"eot": {"reason": decision.reason, "rechecks": rechecks,
                               "waited_ms": int((now - msgs[0]["created_at"]).total_seconds() * 1000), "messages": len(msgs)}}
            signals["read_sent"] = await self._read_now(bid, cid, version, biz, msgs, signals["eot"]["waited_ms"])
            detail = {"eot_reason": decision.reason, **signals["eot"]}
            trigger = "takeover" if cause == "takeover" else "turn_detection"          # the clock woke it, or the customer did
            run = await start_run(self.db, bid, cid, trigger, detail, msgs[0]["created_at"])
            try:
                outcome = await self.agent.run_turn(bid, cid, version, signals=signals)
            except Exception as e:
                await finish_run(self.db, bid, run, outcome="failed", reason=f"{type(e).__name__}: {e}")
                raise
            await finish_with(self.db, bid, run, outcome)
            log.info("%s", turn_line(outcome, signals["eot"]["reason"], signals["eot"]["waited_ms"]))

    async def _read_now(self, bid: uuid.UUID, cid: uuid.UUID, version: int, biz: dict[str, Any], msgs: list[dict[str, Any]], waited_ms: int) -> bool:
        """The customer has stopped writing and the AI will look at it: show the read ticks now, after the rest of a believable reading
        time, not after the model has finished. The wait for the end of the turn counts as reading time."""
        wa = next((m["wa_message_id"] for m in reversed(msgs) if m["wa_message_id"]), None)
        if not wa:
            return False
        delay = max(0, int(sample(biz["timing_params"]["read_delay"], random.Random()) - waited_ms))      # noqa: S311
        async with self.db.tenant(bid) as c:
            await emit(c, "outbound.action_requested", {"conversation_id": cid, "action": "mark_read", "version": version, "inbound_wa_id": wa},
                       business_id=bid, ordering_key=f"conv:{cid}", entity_key=f"conversation:{cid}", entity_version=version,
                       run_at=datetime.now(UTC) + timedelta(milliseconds=delay))
        return True

    async def nudge(self, job: Job) -> None:
        """FR-SL-8: at most one in-window nudge after the customer goes quiet; never when selling has stopped."""
        p = job.spec.payload
        bid, cid, version = job.spec.business_id, uuid.UUID(p["conversation_id"]), int(p["version"])
        assert bid is not None
        async with self.db.tenant(bid) as c:
            conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s", (cid,))).fetchone()
            if conv is None or conv["version"] != version or conv["selling_stopped"] or conv["lead_stage"] in ("won", "lost", "new"):
                return          # the scheduler already marked nudge_sent_at when it requested this (at most one nudge)
        run = await start_run(self.db, bid, cid, "follow_back", {"nudge": True})
        try:
            outcome = await self.agent.run_turn(bid, cid, version, trigger="nudge", signals={"nudge": True})
        except Exception as e:
            await finish_run(self.db, bid, run, outcome="failed", reason=f"{type(e).__name__}: {e}")
            raise
        await finish_with(self.db, bid, run, outcome)

    async def _recent_turns(self, bid: uuid.UUID, cid: uuid.UUID) -> int:
        async with self.db.tenant(bid) as c:
            n = (await (await c.execute(
                "SELECT count(*) AS n FROM turns WHERE conversation_id=%s AND created_at > now() - interval '10 minutes'", (cid,))).fetchone())["n"]
        return int(n)

    async def _tell_founder_unusually_active(self, bid: uuid.UUID, cid: uuid.UUID, turns: int) -> None:
        """One alert per conversation while it stays this active. It carries counts only, never what was said. A failure to alert never touches the reply."""
        try:
            async with self.db.system_tx() as s:
                await raise_alert(s, "medium", "conversation_unusually_active",
                                  f"A conversation had {turns} AI turns in 10 minutes. Replies are not limited; check it is a person and not a loop between two automatic replies.",
                                  business_id=bid, dedup_key=str(cid), detail={"conversation_id": str(cid), "turns_10min": turns})
        except Exception:  # noqa: BLE001
            log.exception("could not raise the unusually-active alert")

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
            await raise_alert(s, "high", "turn_failed", "AI turn failed repeatedly; conversation handed off", business_id=bid,
                              detail={"conversation_id": str(cid)})
