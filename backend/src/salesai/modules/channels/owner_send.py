"""Gateway: sends the alerts and replies the agent's owner loop asked for (ADR 0025).

The agent decides what to tell the owner and records it as a queued `owner_messages` row; this handler, on the gateway's
send queue, picks the sending number, sends, and records the outcome. Hard rules live here, at send time, as for every
other outbound message: free-form text only inside the owner's 24-hour window (INV-8). A temporary failure retries only
this one message; a permanent one is recorded and raised to the platform team (INV-12). At-least-once delivery is safe:
a message that is no longer queued is skipped."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from salesai.alerts import raise_alert
from salesai.db import Database
from salesai.modules.channels.registry import ChannelRegistry
from salesai.queue.base import Job
from salesai.rules import window_open

log = logging.getLogger("salesai.owner_send")


class OwnerSender:
    def __init__(self, db: Database, channels: ChannelRegistry):
        self.db, self.channels = db, channels

    async def send(self, job: Job) -> None:
        bid = job.spec.business_id
        assert bid is not None
        mid = uuid.UUID(str(job.spec.payload["owner_message_id"]))
        async with self.db.tenant(bid) as c:
            m = await (await c.execute("SELECT * FROM owner_messages WHERE id=%s FOR UPDATE", (mid,))).fetchone()
            if m is None or m["status"] != "queued":
                return                                                           # sent already, or failed for good
            u = await (await c.execute(
                """SELECT bu.last_inbound_at, a.phone, a.language FROM business_users bu JOIN accounts a ON a.id=bu.account_id
                   WHERE bu.id=%s""", (m["business_user_id"],))).fetchone()
            num = await (await c.execute("SELECT id FROM whatsapp_numbers WHERE status='connected' ORDER BY created_at LIMIT 1")).fetchone()
        if u is None:
            await self._finish(bid, mid, None, error="the owner no longer exists", number_id=None)
            return
        if m["kind"] == "text" and not window_open(u["last_inbound_at"]):        # INV-8, checked again where the hard rules live
            await self._finish(bid, mid, None, error="window_closed: the owner's 24-hour window closed before this was sent", number_id=None)
            await self._alert(bid, "owner_alert_failed", "Could not alert the owner: the 24-hour window had closed", {"purpose": m["purpose"]})
            return
        if num is None:                         # e.g. the business number is the thing that disconnected: use the platform sender
            ref, channel = self.channels.platform_sender()
        else:
            ref, channel = await self.channels.number(bid, num["id"])
        to = u["phone"].lstrip("+")
        if m["kind"] == "template":
            res = await channel.send_template(ref, to, m["template_name"] or "", m["language"] or u["language"], list(m["params"] or []))
        else:
            res = await channel.send_text(ref, to, m["body"] or "")
        if res.ok:
            await self._finish(bid, mid, res.message_id, error=None, number_id=num["id"] if num else None)
            return
        await self._alert(bid, "owner_alert_failed", f"Could not alert the owner: {res.error}", {"purpose": m["purpose"]})
        if res.retryable:
            raise RuntimeError(res.error or "owner alert failed")                  # the queue retries this one message with backoff
        await self._finish(bid, mid, None, error=res.error or "send failed", number_id=num["id"] if num else None)

    async def _finish(self, bid: uuid.UUID, mid: uuid.UUID, wa_id: str | None, *, error: str | None, number_id: uuid.UUID | None) -> None:
        async with self.db.tenant(bid) as c:
            await c.execute(
                """UPDATE owner_messages SET status=%s, wa_message_id=%s, error=%s, whatsapp_number_id=%s,
                          sent_at = CASE WHEN %s THEN now() ELSE sent_at END WHERE id=%s AND status='queued'""",
                ("failed" if error else "sent", wa_id, error, number_id, error is None, mid))

    async def _alert(self, bid: uuid.UUID, kind: str, message: str, detail: dict[str, Any]) -> None:
        async with self.db.system_tx() as s:
            await raise_alert(s, "medium", kind, message, business_id=bid, detail=detail)

