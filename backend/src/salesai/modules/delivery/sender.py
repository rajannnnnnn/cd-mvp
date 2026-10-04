"""Outbound sender: executes one timed action (read receipt + typing, reaction, text part) from the
`outbound.actions` queue. Every action RE-CHECKS at execution time: conversation version (INV-7), the 24-hour
window (INV-8), AI pause / opt-out / personal contact / disconnection (INV-10), and the per-number rate
limit. Permanent failures become a handoff + operator alert (INV-12), never silence."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from salesai import ratelimit
from salesai.db import Database
from salesai.events.outbox import emit
from salesai.modules.channels import ChannelRegistry, SendResult
from salesai.modules.handoffs import create_handoff
from salesai.obs import SEND_RESULTS, bind
from salesai.queue.base import Defer, Job, NonRetryable
from salesai.rules import ai_block_reason, window_open

log = logging.getLogger("salesai.sender")


class TransientSendError(Exception):
    pass


class OutboundSender:
    def __init__(self, db: Database, channels: ChannelRegistry, *, rate_per_s: float = 5.0, burst: float = 5.0):
        self.db, self.channels = db, channels
        self.rate_per_s, self.burst = rate_per_s, burst

    async def execute(self, job: Job) -> None:
        p = job.spec.payload
        bid = job.spec.business_id
        assert bid is not None
        cid = uuid.UUID(p["conversation_id"])
        action = p["action"]
        with bind(business_id=bid, conversation_id=cid):
            async with self.db.tenant(bid) as c:
                conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s", (cid,))).fetchone()
                if conv is None:
                    return
                cust = await (await c.execute("SELECT * FROM customers WHERE id=%s", (conv["customer_id"],))).fetchone()
                biz = await (await c.execute("SELECT * FROM businesses WHERE id=%s", (bid,))).fetchone()
                num = await (await c.execute("SELECT * FROM whatsapp_numbers WHERE id=%s", (conv["whatsapp_number_id"],))).fetchone()
                msg = None
                if p.get("message_id"):
                    msg = await (await c.execute("SELECT * FROM messages WHERE id=%s", (p["message_id"],))).fetchone()

            # --- send-time checks
            if conv["version"] != p["version"]:                                      # INV-7
                await self._cancel(bid, msg, "superseded")
                return
            block = ai_block_reason(business=biz, conv=conv, customer=cust, number=num, ignore_pause=bool(p.get("allow_while_paused")))
            if block:                                                                 # INV-10
                await self._cancel(bid, msg, block)
                return
            if action in ("send_text", "send_reaction") and not window_open(conv["last_inbound_at"]):   # INV-8
                await self._fail(bid, cid, msg, "window_closed", "24-hour customer service window is closed; a template is required")
                return

            wait = await ratelimit.take(self.db, f"num:{num['id']}", rate_per_s=self.rate_per_s, burst=self.burst)
            if wait > 0:
                raise Defer(wait, "per-number rate limit")

            ref, channel = await self.channels.number(bid, num["id"])
            if action in ("mark_read", "typing"):
                if p.get("inbound_wa_id"):
                    res = await channel.mark_read(ref, p["inbound_wa_id"], typing=True)
                    if not res.ok and res.permanent == "token_invalid":
                        await self._number_problem(bid, num["id"], "disconnected", res)
                async with self.db.tenant(bid) as c:
                    await c.execute("UPDATE conversations SET state='typing' WHERE id=%s AND version=%s", (cid, p["version"]))
                return
            if action == "send_reaction":
                res = await channel.send_reaction(ref, cust["wa_id"], p["inbound_wa_id"], p["emoji"])
            else:
                res = await channel.send_text(ref, cust["wa_id"], msg["body"])
            await self._record(bid, cid, msg, res, num["id"], last=bool(p.get("last_part")) or action == "send_reaction", version=p["version"])

    # ------------------------------------------------------------------
    async def _record(self, bid: uuid.UUID, cid: uuid.UUID, msg: dict[str, Any] | None, res: SendResult, number_id: uuid.UUID,
                      last: bool, version: int) -> None:
        if res.ok:
            SEND_RESULTS.labels("ok").inc()
            async with self.db.tenant(bid) as c:
                if msg:
                    await c.execute("UPDATE messages SET status='sent', wa_message_id=%s, sent_at=now() WHERE id=%s", (res.message_id, msg["id"]))
                await c.execute(
                    "UPDATE conversations SET last_outbound_at=now(), state = CASE WHEN %s AND version=%s THEN 'idle' ELSE state END WHERE id=%s",
                    (last, version, cid))
            return
        if res.retryable:
            SEND_RESULTS.labels("retry").inc()
            if res.retry_after_s:                      # rate limited by Meta: wait, don't burn attempts
                raise Defer(res.retry_after_s, res.error or "rate limited")
            raise TransientSendError(res.error or "transient send error")
        SEND_RESULTS.labels("failed").inc()
        if res.permanent in ("token_invalid", "restricted"):
            await self._number_problem(bid, number_id, "disconnected" if res.permanent == "token_invalid" else "restricted", res)
        await self._fail(bid, cid, msg, res.permanent or "other", f"{res.error_code}: {res.error}")
        raise NonRetryable(f"send failed permanently: {res.permanent}")

    async def _cancel(self, bid: uuid.UUID, msg: dict[str, Any] | None, why: str) -> None:
        if msg and msg["status"] == "queued":
            async with self.db.tenant(bid) as c:
                await c.execute("UPDATE messages SET status='cancelled', error=%s WHERE id=%s AND status='queued'", (why, msg["id"]))
        log.info("outbound action dropped: %s", why)

    async def _fail(self, bid: uuid.UUID, cid: uuid.UUID, msg: dict[str, Any] | None, kind: str, detail: str) -> None:
        """INV-12: a message that cannot be delivered becomes a handoff and an operator alert."""
        async with self.db.tenant(bid) as c:
            if msg:
                await c.execute("UPDATE messages SET status='failed', error=%s WHERE id=%s", (f"{kind}: {detail}"[:500], msg["id"]))
            await create_handoff(c, bid, cid, "system_failure", f"Reply could not be delivered ({kind}): {detail}", pause_minutes=None)
        import json
        async with self.db.system_tx() as s:
            await s.execute("INSERT INTO operator_alerts (business_id, severity, kind, message, detail) VALUES (%s,'warning','send_failed',%s,%s::jsonb)",
                            (bid, f"Outbound message failed: {kind}", json.dumps({"detail": detail[:300], "conversation_id": str(cid)})))

    async def _number_problem(self, bid: uuid.UUID, number_id: uuid.UUID, status: str, res: SendResult) -> None:
        async with self.db.tenant(bid) as c:
            await c.execute("UPDATE whatsapp_numbers SET status=%s, status_reason=%s WHERE id=%s", (status, (res.error or "")[:200], number_id))
            await emit(c, "number.status_changed", {"whatsapp_number_id": number_id, "status": status}, business_id=bid,
                       ordering_key=f"biz:{bid}")

