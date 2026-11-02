"""Gateway: sends sign-in codes the web component asked for (ADR 0025).

The web component never touches Meta. It writes an `otp_deliveries` request and queues its id; this handler, on the
gateway's send queue, sends the code from the platform's own sender and records the answer for the web side to read.
At-least-once delivery is safe: a request that is no longer pending is skipped. The plain code is cleared the moment it
is sent, fails or expires, so it never outlives the attempt."""
from __future__ import annotations

import logging
import uuid

from salesai.db import Database
from salesai.modules.channels.registry import ChannelRegistry
from salesai.phone import wa_id
from salesai.queue.base import Job

log = logging.getLogger("salesai.otp")


class OtpSender:
    def __init__(self, db: Database, channels: ChannelRegistry):
        self.db, self.channels = db, channels

    async def send_otp(self, job: Job) -> None:
        did = uuid.UUID(str(job.spec.payload["delivery_id"]))
        async with self.db.system_tx() as c:
            row = await (await c.execute("SELECT *, (expires_at <= now()) AS expired FROM otp_deliveries WHERE id=%s FOR UPDATE", (did,))).fetchone()
            if row is None or row["status"] != "pending":
                return                                                     # answered already, or the web side gave up
            if row["expired"] or not row["code"]:
                await c.execute("UPDATE otp_deliveries SET status='failed', error='expired', code=NULL WHERE id=%s", (did,))
                return
            code, phone, language = row["code"], row["phone"], row["language"]
        ref, channel = self.channels.platform_sender()
        res = await channel.send_template(ref, wa_id(phone), "otp_login", language, [code])
        async with self.db.system_tx() as c:
            if res.ok:
                await c.execute("UPDATE otp_deliveries SET status='sent', code=NULL, sent_at=now() WHERE id=%s AND status='pending'", (did,))
                return
            if res.retryable:
                raise RuntimeError(res.error or "sign-in code delivery failed")      # the queue retries with backoff; the code stays until then
            log.error("sign-in code delivery failed: %s", res.error)
            await c.execute("UPDATE otp_deliveries SET status='failed', error=%s, code=NULL WHERE id=%s AND status='pending'",
                            ((res.error or "delivery failed")[:300], did))
