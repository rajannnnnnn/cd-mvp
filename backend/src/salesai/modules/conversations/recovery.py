"""The agent's recovery sweep (ADR 0035). Everything the agent has in flight lives in the database, not in a process or a queue:
a customer message and whether it was answered, a planned reply and whether it was sent, a hand-off row and whether it was
picked up. The queue is how work normally moves; this sweep is the safety net underneath it. It runs on the agent's scheduler,
on start and then every half minute, and compares what the database says should be happening with what is, then re-drives the
difference. It works the same whatever the queue backend is, and whether the queue lost the work or never had it.

Each repair is safe to repeat: an end-of-turn check for an answered conversation finds nothing to do, a send is refused by the
sender unless the message is still queued, and a hand-off is acted on once. A conversation that keeps failing is not retried
for ever: after a few tries the owner is handed the conversation and the founder is alerted, so a customer is never left in silence
(INV-12). A conversation the AI must not answer (INV-10) is never touched."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from salesai.alerts import raise_alert
from salesai.db import Database
from salesai.events.outbox import emit
from salesai.modules.handoffs import create_handoff
from salesai.rules import ai_block_reason

log = logging.getLogger("salesai.recovery")


class Reconciler:
    def __init__(self, db: Database, *, stuck_after_s: float = 90, retry_after_s: float = 180, max_attempts: int = 3,
                 queued_message_s: float = 120, mailbox_s: float = 60, state_stuck_s: float = 600,
                 business_id: uuid.UUID | None = None):
        self.db = db
        self.only = business_id                                 # None = every shop; a value limits the sweep to one (operator tools, tests)
        self.stuck_after_s, self.retry_after_s, self.max_attempts = stuck_after_s, retry_after_s, max_attempts
        self.queued_message_s, self.mailbox_s, self.state_stuck_s = queued_message_s, mailbox_s, state_stuck_s

    async def sweep(self) -> dict[str, int]:
        out = {"retriggered": 0, "gave_up": 0, "resent": 0, "mailbox_redriven": 0, "owner_messages_redriven": 0, "state_reset": 0}
        for step in (self._customer_messages, self._queued_replies, self._agent_mailbox, self._owner_messages, self._stuck_state):
            try:
                for k, v in (await step()).items():
                    out[k] += v
            except Exception:  # noqa: BLE001 - one failing repair must not stop the others
                log.exception("recovery step %s failed", step.__name__)
        if any(out.values()):
            log.warning("recovery sweep: %s", {k: v for k, v in out.items() if v})
        return out

    # ------------------------------------------------------------------ a customer message nobody is working on
    async def _customer_messages(self) -> dict[str, int]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT cv.business_id, cv.id, cv.version, cv.recovery_attempts FROM conversations cv
                    WHERE EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = cv.id AND m.direction = 'in' AND m.sender = 'customer'
                                     AND NOT m.answered AND m.created_at < now() - make_interval(secs => %s))
                      AND (cv.last_recovery_at IS NULL OR cv.last_recovery_at < now() - make_interval(secs => %s))
                      AND (%s::uuid IS NULL OR cv.business_id = %s)
                    ORDER BY cv.updated_at LIMIT 200""", (self.stuck_after_s, self.retry_after_s, self.only, self.only))).fetchall()
        done = {"retriggered": 0, "gave_up": 0}
        for r in rows:
            bid, cid = r["business_id"], r["id"]
            async with self.db.tenant(bid) as c:
                conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s FOR UPDATE", (cid,))).fetchone()
                if conv is None:
                    continue
                msgs = await (await c.execute(
                    "SELECT id FROM messages WHERE conversation_id=%s AND direction='in' AND sender='customer' AND NOT answered ORDER BY created_at DESC, id", (cid,))).fetchall()
                if not msgs:
                    continue                                                    # answered while we were looking
                biz = await (await c.execute("SELECT * FROM businesses WHERE id=%s", (bid,))).fetchone()
                cust = await (await c.execute("SELECT * FROM customers WHERE id=%s", (conv["customer_id"],))).fetchone()
                num = await (await c.execute("SELECT id, business_id, channel, phone_number_id, display_phone, verified_name, status, quality_rating, coexistence "
                                             "FROM whatsapp_numbers WHERE id=%s", (conv["whatsapp_number_id"],))).fetchone()
                if biz is None or cust is None or num is None or ai_block_reason(business=biz, conv=conv, customer=cust, number=num):
                    continue                                                    # INV-10: not ours to answer, and the normal path releases it
                if conv["recovery_attempts"] >= self.max_attempts:
                    await create_handoff(c, bid, cid, "system_failure", "The AI could not get to this conversation, even after being restarted", pause_minutes=120)
                    await c.execute("UPDATE messages SET answered=true WHERE conversation_id=%s AND direction='in' AND NOT answered", (cid,))
                    await c.execute("UPDATE conversations SET state='idle', last_recovery_at=now() WHERE id=%s", (cid,))
                    gave_up = True
                else:
                    await emit(c, "conversation.eot_recheck", {"conversation_id": cid, "message_id": msgs[0]["id"], "version": conv["version"], "rechecks": 0},
                               business_id=bid, ordering_key=f"conv:{cid}", entity_key=f"conversation:{cid}", entity_version=conv["version"])
                    await c.execute("UPDATE conversations SET recovery_attempts = recovery_attempts + 1, last_recovery_at = now() WHERE id=%s", (cid,))
                    gave_up = False
            if gave_up:
                async with self.db.system_tx() as s:
                    await raise_alert(s, "high", "stuck_conversation", "A customer's message could not be answered even after recovery; the owner was handed the chat",
                                      business_id=bid, detail={"conversation_id": str(cid)})
                done["gave_up"] += 1
            else:
                done["retriggered"] += 1
        return done

    # ------------------------------------------------------------------ a reply that was planned and never sent
    async def _queued_replies(self) -> dict[str, int]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT m.id, m.business_id, m.conversation_id, m.turn_id, m.kind, m.body, m.reply_to_wa_id, t.conversation_version AS version,
                          cv.version AS current_version,
                          NOT EXISTS (SELECT 1 FROM messages o WHERE o.turn_id = m.turn_id AND o.direction = 'out' AND o.status = 'queued' AND o.id <> m.id) AS only_one
                     FROM messages m JOIN turns t ON t.id = m.turn_id AND t.business_id = m.business_id
                     JOIN conversations cv ON cv.id = m.conversation_id
                    WHERE m.direction = 'out' AND m.sender = 'ai' AND m.status = 'queued' AND m.kind IN ('text', 'reaction')
                      AND m.created_at < now() - make_interval(secs => %s) AND (%s::uuid IS NULL OR m.business_id = %s)
                    ORDER BY m.created_at LIMIT 200""", (self.queued_message_s, self.only, self.only))).fetchall()
        n = 0
        for r in rows:
            if r["current_version"] != r["version"]:                    # INV-7: the customer has written since; this reply answers an old state
                async with self.db.tenant(r["business_id"]) as c:
                    await c.execute("UPDATE messages SET status='cancelled', error='superseded' WHERE id=%s AND status='queued'", (r["id"],))
                continue
            payload: dict[str, Any] = {"conversation_id": r["conversation_id"], "turn_id": r["turn_id"], "version": r["version"], "message_id": r["id"],
                                       "allow_while_paused": False, "last_part": bool(r["only_one"])}
            if r["kind"] == "reaction":
                payload.update(action="send_reaction", emoji=r["body"], inbound_wa_id=r["reply_to_wa_id"])
            else:
                payload["action"] = "send_text"
            async with self.db.tenant(r["business_id"]) as c:
                await emit(c, "outbound.action_requested", payload, business_id=r["business_id"], ordering_key=f"conv:{r['conversation_id']}",
                           entity_key=f"conversation:{r['conversation_id']}", entity_version=r["version"])
            n += 1
        return {"resent": n}

    # ------------------------------------------------------------------ what the gateway handed over and nobody picked up
    async def _agent_mailbox(self) -> dict[str, int]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                "SELECT id, business_id FROM channel_events WHERE status = 'pending' AND created_at < now() - make_interval(secs => %s)"
                " AND (%s::uuid IS NULL OR business_id = %s) ORDER BY created_at LIMIT 200", (self.mailbox_s, self.only, self.only))).fetchall()
        for r in rows:
            async with self.db.tenant(r["business_id"]) as c:
                await emit(c, "channel.event_received", {"channel_event_id": r["id"]}, business_id=r["business_id"])
        return {"mailbox_redriven": len(rows)}

    async def _owner_messages(self) -> dict[str, int]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT id, business_id, business_user_id FROM owner_messages WHERE direction = 'out' AND status = 'queued' AND purpose <> 'chat'
                      AND created_at < now() - make_interval(secs => %s) AND (%s::uuid IS NULL OR business_id = %s)
                    ORDER BY created_at LIMIT 200""", (self.mailbox_s, self.only, self.only))).fetchall()
        for r in rows:
            async with self.db.tenant(r["business_id"]) as c:
                await emit(c, "owner.message_requested", {"owner_message_id": r["id"]}, business_id=r["business_id"], ordering_key=f"owner:{r['business_user_id']}")
        return {"owner_messages_redriven": len(rows)}

    # ------------------------------------------------------------------ state that must not be left behind
    async def _stuck_state(self) -> dict[str, int]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT cv.business_id, cv.id FROM conversations cv
                    WHERE cv.state <> 'idle' AND cv.updated_at < now() - make_interval(secs => %s)
                      AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = cv.id AND
                                      ((m.direction = 'in' AND m.sender = 'customer' AND NOT m.answered) OR (m.direction = 'out' AND m.status = 'queued')))
                      AND (%s::uuid IS NULL OR cv.business_id = %s)
                    LIMIT 200""", (self.state_stuck_s, self.only, self.only))).fetchall()
        for r in rows:
            async with self.db.tenant(r["business_id"]) as c:
                await c.execute("UPDATE conversations SET state='idle' WHERE id=%s AND state <> 'idle'", (r["id"],))
        return {"state_reset": len(rows)}
