"""Mail to shop owners (ADR 0054). `enqueue` writes one row in the SAME transaction as the event it is about, so the mail cannot be lost and can
never hold that event up. `MailDispatcher.dispatch` (run by the web scheduler, right after the founder's alerts) sends what is due: a few at a
time under a rate budget, with retries, one mail per `dedupe_key`, and a row per outcome. A shop without a billing address is skipped, never
an error. Nothing here sends to anyone but the one address a shop gave for billing and support."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from salesai.db import Conn, Database, jsonb
from salesai.modules.mail.templates import CATALOG, render

log = logging.getLogger("salesai.mail")
LEASE_MINUTES = 5            # a row picked up but not finished is retried after this: a crashed sender never loses a mail


def _one_line(s: Any) -> str:
    return " ".join(str(s).split())


async def enqueue(conn: Conn, business_id: Any, template: str, data: dict[str, Any], dedupe_key: str) -> bool:
    """Queue one mail inside the caller's transaction. Returns False when this exact mail was already queued (a retried event)."""
    purpose = CATALOG[template].purpose            # raises for an unknown template, so a typo is caught at once
    data = json.loads(json.dumps(data, default=str))      # dates and ids become text, as the outbox stores plain JSON
    row = await (await conn.execute(
        """INSERT INTO mail_outbox (business_id, template, purpose, data, dedupe_key) VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (dedupe_key) DO NOTHING RETURNING id""", (business_id, template, purpose, jsonb(data), dedupe_key))).fetchone()
    return row is not None


class MailDispatcher:
    def __init__(self, db: Database, *, deliver: Callable[[EmailMessage], str | None], sender_for: Callable[[str], str], bcc_for: Callable[[str], list[str]],
                 site_url: str, per_minute: int = 60, max_attempts: int = 5, enabled: bool = True):
        self.db, self.deliver, self.sender_for, self.bcc_for = db, deliver, sender_for, bcc_for
        self.site_url, self.per_minute, self.max_attempts, self.enabled = site_url, per_minute, max_attempts, enabled

    async def dispatch(self) -> int:
        """Send what is due. The budget is a share of the provider's rate, so founder alerts always have room; it runs every half minute."""
        budget = max(1, self.per_minute // 2)
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                f"""UPDATE mail_outbox o SET next_attempt_at = now() + interval '{LEASE_MINUTES} minutes'
                    FROM (SELECT id FROM mail_outbox WHERE status='pending' AND next_attempt_at <= now() ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED) due
                    WHERE o.id = due.id RETURNING o.id, o.business_id, o.template, o.purpose, o.data, o.attempts""", (budget,))).fetchall()      # noqa: S608
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            info = {r["id"]: r for r in await (await c.execute(
                """SELECT o.id, b.name AS shop, b.billing_email,
                          (SELECT u.name FROM business_users u WHERE u.business_id = b.id AND u.role = 'owner' ORDER BY u.created_at LIMIT 1) AS owner
                   FROM mail_outbox o JOIN businesses b ON b.id = o.business_id WHERE o.id = ANY(%s)""", (ids,))).fetchall()}
        sent = 0
        for r in rows:
            sent += await self._one(r, info.get(r["id"]) or {})
        return sent

    async def _one(self, r: dict[str, Any], who: dict[str, Any]) -> int:
        oid = r["id"]
        if not self.enabled:
            return await self._finish(oid, "skipped", error="client mail is not set up (no CLIENT_EMAIL_FROM)")
        to = (who.get("billing_email") or "").strip()
        if not to:
            return await self._finish(oid, "skipped", error="the shop has no billing email")
        try:
            data = {**r["data"], "shop": who.get("shop") or "", "name": (who.get("owner") or "").split(" ")[0]}
            mail = render(r["template"], data, self.site_url)
            msg = EmailMessage()
            sender = self.sender_for(r["purpose"])
            msg["Subject"], msg["From"], msg["To"], msg["Reply-To"] = _one_line(mail.subject)[:200], _one_line(sender), _one_line(to), _one_line(sender)
            if bcc := self.bcc_for(r["purpose"]):
                msg["Bcc"] = ", ".join(bcc)
            msg.set_content(mail.text)
            msg.add_alternative(mail.html, subtype="html")
            ref = await asyncio.to_thread(self.deliver, msg)
        except Exception as e:  # noqa: BLE001  one mail failing must not stop the rest
            return await self._retry(oid, int(r["attempts"]), f"{type(e).__name__}: {e}")
        log.info("mail sent (%s/%s)%s", r["purpose"], r["template"], f" ref {ref}" if ref else "")
        return await self._finish(oid, "sent", ref=ref)

    async def _finish(self, oid: int, status: str, *, ref: str | None = None, error: str | None = None) -> int:
        async with self.db.system_tx() as c:
            await c.execute("UPDATE mail_outbox SET status=%s, provider_ref=%s, error=%s, sent_at = CASE WHEN %s='sent' THEN now() END WHERE id=%s",
                            (status, ref, error, status, oid))
        if status == "skipped":
            log.info("mail skipped: %s", error)
        return 1 if status == "sent" else 0

    async def _retry(self, oid: int, attempts: int, error: str) -> int:
        attempts += 1
        final = attempts >= self.max_attempts
        log.warning("mail %s (attempt %d): %s", "FAILED for good" if final else "failed, will retry", attempts, error[:160])
        async with self.db.system_tx() as c:
            await c.execute(
                "UPDATE mail_outbox SET attempts=%s, error=%s, status=%s, next_attempt_at = now() + make_interval(mins => %s) WHERE id=%s",
                (attempts, error[:300], "failed" if final else "pending", min(2 ** attempts, 60), oid))
        return 0
