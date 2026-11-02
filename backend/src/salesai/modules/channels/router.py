"""Gateway: reads a stored webhook and hands the agent channel-neutral events (ADR 0025).

This is the gateway's `inbound` part. It is the only code that reads Meta's wire format: it parses the raw request that
`receive` stored, works out which of our numbers each event belongs to, and writes one `channel_events` row per event for
the agent to act on, queuing only the row's id. It never touches a conversation, a message or a handoff.

Safe to repeat: a request already handed over is skipped, and (request, position, number) is unique, so retrying one that
failed half way never duplicates an event. Every step that cannot place an event raises an operator alert (INV-12)."""
from __future__ import annotations

import json
import logging
from typing import Any

from salesai.db import Database, jsonb
from salesai.events.outbox import emit
from salesai.modules.channels.base import (
    AccountEvent,
    ChannelEvent,
    EchoMessage,
    InboundMessage,
    event_to_dict,
)
from salesai.modules.channels.whatsapp import parse_webhook
from salesai.queue.base import Job

log = logging.getLogger("salesai.gateway.inbound")
_KIND = {InboundMessage: "inbound", EchoMessage: "echo", AccountEvent: "account"}   # anything else is a delivery status


def event_phone_number_id(ev: ChannelEvent) -> str | None:
    if isinstance(ev, InboundMessage):
        return ev.to_phone_number_id
    if isinstance(ev, EchoMessage):
        return ev.from_phone_number_id
    return ev.phone_number_id


def _ordering_key(number_id: Any, ev: ChannelEvent) -> str:
    """Events about one customer are handled in order; different customers and account events do not wait for each other."""
    who = ev.from_id if isinstance(ev, InboundMessage) else ev.to_id if isinstance(ev, EchoMessage) \
        else getattr(ev, "recipient_id", None)
    return f"cust:{number_id}:{who}" if who else f"num:{number_id}"


class WebhookRouter:
    def __init__(self, db: Database):
        self.db = db

    # ---------------------------------------------------------------- job handler
    async def route_webhook(self, job: Job) -> None:
        wid = job.spec.payload["webhook_event_id"]
        async with self.db.system_tx() as c:
            row = await (await c.execute("SELECT payload, signature_valid, processed_at FROM webhook_events WHERE id=%s", (wid,))).fetchone()
        if row is None or not row["signature_valid"] or row["payload"] is None:
            return                                   # never process a request that failed verification or could not be read
        if row["processed_at"] is not None:
            return                                   # already handed over (at-least-once delivery)
        try:
            await self.hand_over(row["payload"], wid)
        except Exception as e:
            async with self.db.system_tx() as c:
                await c.execute("UPDATE webhook_events SET error=%s WHERE id=%s", (str(e)[:500], wid))
            raise
        async with self.db.system_tx() as c:
            await c.execute("UPDATE webhook_events SET processed_at=now(), error=NULL WHERE id=%s", (wid,))

    async def hand_over(self, payload: dict[str, Any], webhook_event_id: int | None = None) -> None:
        for ordinal, ev in enumerate(parse_webhook(payload)):
            if isinstance(ev, AccountEvent):
                numbers = await self._numbers_for_account(ev)
            else:
                pn = event_phone_number_id(ev)
                one = await self._resolve_number(pn) if pn else None
                numbers = [one] if one else []
            if not numbers:
                await self._unknown_number(event_phone_number_id(ev), ev)
                continue
            for number in numbers:
                await self._hand_to_agent(number, ev, webhook_event_id, ordinal)

    async def _hand_to_agent(self, number: dict[str, Any], ev: ChannelEvent, webhook_event_id: int | None, ordinal: int) -> None:
        bid = number["business_id"]
        async with self.db.tenant(bid) as c:
            row = await (await c.execute(
                """INSERT INTO channel_events (business_id, number_id, webhook_event_id, ordinal, kind, event)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (webhook_event_id, ordinal, number_id) DO NOTHING RETURNING id""",
                (bid, number["id"], webhook_event_id, ordinal, _KIND.get(type(ev), "status"), jsonb(event_to_dict(ev))))).fetchone()
            if row is not None:                      # None: handed over already by an earlier attempt
                await emit(c, "channel.event_received", {"channel_event_id": row["id"]}, business_id=bid,
                           ordering_key=_ordering_key(number["id"], ev))

    # ---------------------------------------------------------------- which of our numbers is this about?
    async def _resolve_number(self, phone_number_id: str) -> dict[str, Any] | None:
        async with self.db.system_tx() as c:
            return await (await c.execute(
                "SELECT id, business_id, display_phone, status, channel, phone_number_id FROM whatsapp_numbers WHERE phone_number_id=%s",
                (phone_number_id,))).fetchone()

    async def _numbers_for_account(self, ev: AccountEvent) -> list[dict[str, Any]]:
        """Account webhooks usually name the WhatsApp Business Account, not a phone number (FR-ON-4). Most specific
        first: the phone-number id, then a display phone within that account, then every number on the account — on
        the platform's own account that rightly means every pilot shop, since they all go down together."""
        if ev.phone_number_id:
            one = await self._resolve_number(ev.phone_number_id)
            return [one] if one else []
        digits = "".join(ch for ch in str(ev.detail.get("display_phone_number") or ev.detail.get("phone_number") or "") if ch.isdigit())
        async with self.db.system_tx() as c:
            if digits:
                rows = await (await c.execute(
                    """SELECT id, business_id, display_phone, status, channel, phone_number_id FROM whatsapp_numbers
                        WHERE regexp_replace(display_phone, '[^0-9]', '', 'g') = %s AND (%s::text IS NULL OR waba_id = %s)""",
                    (digits, ev.waba_id, ev.waba_id))).fetchall()
                if rows:
                    return list(rows)
            if ev.waba_id:
                return list(await (await c.execute(
                    "SELECT id, business_id, display_phone, status, channel, phone_number_id FROM whatsapp_numbers WHERE waba_id=%s",
                    (ev.waba_id,))).fetchall())
        return []

    async def _unknown_number(self, pn: str | None, ev: ChannelEvent) -> None:
        """INV-12: never silent. A webhook for a number we do not know is an operator alert."""
        waba = ev.waba_id if isinstance(ev, AccountEvent) else None
        log.error("webhook for unknown phone_number_id %s (business account %s)", pn, waba)
        async with self.db.system_tx() as c:
            exists = await (await c.execute(
                "SELECT 1 FROM operator_alerts WHERE kind='unknown_number' AND detail->>'phone_number_id' IS NOT DISTINCT FROM %s"
                " AND detail->>'waba_id' IS NOT DISTINCT FROM %s AND resolved_at IS NULL",
                (pn, waba))).fetchone()
            if not exists:
                await c.execute(
                    "INSERT INTO operator_alerts (severity, kind, message, detail) VALUES ('warning','unknown_number',%s,%s::jsonb)",
                    (f"Webhook received for unknown WhatsApp number id {pn} (business account {waba})",
                     json.dumps({"phone_number_id": pn, "waba_id": waba})))
