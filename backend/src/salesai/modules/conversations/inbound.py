"""Event router: Meta webhook payload -> messages, versions, pauses, statuses, account events.
Runs as the `route_webhook` job on inbound.events. Idempotent: reprocessing a payload never produces
duplicate messages or side effects (INV-6)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from salesai.db import Database
from salesai.events.outbox import emit
from salesai.modules.channels import (
    AccountEvent, ChannelEvent, EchoMessage, InboundMessage, StatusUpdate,
)
from salesai.modules.channels.whatsapp import parse_webhook
from salesai.modules.conversations.state import is_opt_in, is_opt_out
from salesai.obs import bind
from salesai.queue.base import Job, Queue

log = logging.getLogger("salesai.inbound")
_RANK = {"queued": 0, "sent": 1, "delivered": 2, "read": 3}


def event_phone_number_id(ev: ChannelEvent) -> str | None:
    if isinstance(ev, InboundMessage):
        return ev.to_phone_number_id
    if isinstance(ev, EchoMessage):
        return ev.from_phone_number_id
    return ev.phone_number_id


class InboundRouter:
    def __init__(self, db: Database, queue: Queue):
        self.db, self.queue = db, queue

    # ---------------------------------------------------------------- job handler
    async def route_webhook(self, job: Job) -> None:
        wid = job.spec.payload["webhook_event_id"]
        async with self.db.system_tx() as c:
            row = await (await c.execute("SELECT payload FROM webhook_events WHERE id=%s", (wid,))).fetchone()
        if row is None:
            return
        try:
            await self.process_payload(row["payload"])
        except Exception as e:
            async with self.db.system_tx() as c:
                await c.execute("UPDATE webhook_events SET error=%s WHERE id=%s", (str(e)[:500], wid))
            raise
        async with self.db.system_tx() as c:
            await c.execute("UPDATE webhook_events SET processed_at=now(), error=NULL WHERE id=%s", (wid,))

    async def process_payload(self, payload: dict[str, Any]) -> None:
        for ev in parse_webhook(payload):
            pn = event_phone_number_id(ev)
            number = await self._resolve_number(pn) if pn else None
            if number is None:
                await self._unknown_number(pn, ev)
                continue
            with bind(business_id=number["business_id"]):
                if isinstance(ev, InboundMessage):
                    await self._inbound(number, ev)
                elif isinstance(ev, EchoMessage):
                    await self._echo(number, ev)
                elif isinstance(ev, StatusUpdate):
                    await self._status(number, ev)
                elif isinstance(ev, AccountEvent):
                    await self._account(number, ev)

    async def _resolve_number(self, phone_number_id: str) -> dict[str, Any] | None:
        async with self.db.system_tx() as c:
            return await (await c.execute(
                "SELECT id, business_id, display_phone, status, channel FROM whatsapp_numbers WHERE phone_number_id=%s",
                (phone_number_id,))).fetchone()

    async def _unknown_number(self, pn: str | None, ev: ChannelEvent) -> None:
        """INV-12: never silent. A webhook for a number we do not know is an operator alert."""
        log.error("webhook for unknown phone_number_id %s", pn)
        async with self.db.system_tx() as c:
            exists = await (await c.execute(
                "SELECT 1 FROM operator_alerts WHERE kind='unknown_number' AND detail->>'phone_number_id'=%s AND resolved_at IS NULL",
                (pn,))).fetchone()
            if not exists:
                import json
                await c.execute(
                    "INSERT INTO operator_alerts (severity, kind, message, detail) VALUES ('warning','unknown_number',%s,%s::jsonb)",
                    (f"Webhook received for unknown WhatsApp number id {pn}", json.dumps({"phone_number_id": pn})))

    # ---------------------------------------------------------------- inbound customer / owner messages
    async def _inbound(self, number: dict[str, Any], ev: InboundMessage) -> None:
        bid, nid = number["business_id"], number["id"]
        version: int | None = None
        conv_id: uuid.UUID | None = None
        async with self.db.tenant(bid) as c:
            owner = await (await c.execute(
                """SELECT bu.id FROM business_users bu JOIN accounts a ON a.id = bu.account_id
                   WHERE a.phone = %s""", ("+" + ev.from_id,))).fetchone()
            if owner:
                await self._owner_message(c, bid, owner["id"], ev)
                return

            cust = await (await c.execute(
                """INSERT INTO customers (business_id, wa_id, name) VALUES (%s,%s,%s)
                   ON CONFLICT (business_id, wa_id) DO UPDATE
                     SET last_seen_at = now(), name = COALESCE(customers.name, EXCLUDED.name)
                   RETURNING *""", (bid, ev.from_id, ev.sender_name))).fetchone()
            conv = await (await c.execute(
                """INSERT INTO conversations (business_id, customer_id, whatsapp_number_id) VALUES (%s,%s,%s)
                   ON CONFLICT (business_id, customer_id, whatsapp_number_id) DO UPDATE SET updated_at = now()
                   RETURNING id""", (bid, cust["id"], nid))).fetchone()
            conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s FOR UPDATE", (conv["id"],))).fetchone()
            conv_id = conv["id"]

            prev = await (await c.execute(
                "SELECT max(wa_timestamp) AS t FROM messages WHERE conversation_id=%s AND direction='in'", (conv_id,))).fetchone()
            gap_ms = int((ev.timestamp - prev["t"]).total_seconds() * 1000) if prev["t"] else None

            # opt-out / opt-in keywords are recorded and honored (CR-3, INV-10)
            opted_out = cust["opted_out"]
            if is_opt_out(ev.text) and ev.kind == "text":
                await c.execute("UPDATE customers SET opted_out=true, opted_out_at=now() WHERE id=%s", (cust["id"],))
                opted_out = True
            elif is_opt_in(ev.text) and ev.kind == "text" and opted_out:
                await c.execute("UPDATE customers SET opted_out=false, opted_out_at=NULL WHERE id=%s", (cust["id"],))
                opted_out = False
            needs_reply = not (cust["is_personal"] or opted_out or ev.kind == "reaction"
                               or is_opt_out(ev.text) or is_opt_in(ev.text))

            ins = await (await c.execute(
                """INSERT INTO messages (business_id, conversation_id, wa_message_id, direction, sender, kind, body,
                                         payload, reply_to_wa_id, status, gap_ms, answered, wa_timestamp)
                   VALUES (%s,%s,%s,'in','customer',%s,%s,%s,%s,'received',%s,%s,%s)
                   ON CONFLICT (wa_message_id) DO NOTHING RETURNING id""",
                (bid, conv_id, ev.channel_message_id, ev.kind, ev.text, _json(ev.raw), ev.reply_to, gap_ms,
                 not needs_reply, ev.timestamp))).fetchone()
            if ins is None:
                return  # duplicate redelivery: ignored (INV-6)

            now = datetime.now(UTC)
            ts = min(ev.timestamp, now)
            if needs_reply:
                upd = await (await c.execute(
                    """UPDATE conversations SET version = version + 1, state = 'listening',
                              last_inbound_at = GREATEST(COALESCE(last_inbound_at, %s), %s)
                       WHERE id=%s RETURNING version""", (ts, ts, conv_id))).fetchone()
                version = upd["version"]
                await emit(c, "message.received", {"conversation_id": conv_id, "message_id": ins["id"], "version": version},
                           business_id=bid, ordering_key=f"conv:{conv_id}", entity_key=f"conversation:{conv_id}",
                           entity_version=version)
            else:
                await c.execute("UPDATE conversations SET last_inbound_at = GREATEST(COALESCE(last_inbound_at, %s), %s) WHERE id=%s",
                                (ts, ts, conv_id))
        if version is not None and conv_id is not None:
            await self.queue.cancel_superseded(f"conversation:{conv_id}", version)

    async def _owner_message(self, c, bid: uuid.UUID, business_user_id: uuid.UUID, ev: InboundMessage) -> None:  # noqa: ANN001
        ins = await (await c.execute(
            """INSERT INTO owner_messages (business_id, business_user_id, direction, kind, purpose, body, wa_message_id, status)
               VALUES (%s,%s,'in',%s,'chat',%s,%s,'received') ON CONFLICT (wa_message_id) DO NOTHING RETURNING id""",
            (bid, business_user_id, ev.kind if ev.kind in ("text", "audio") else "other", ev.text, ev.channel_message_id))).fetchone()
        if ins is None:
            return
        await c.execute("UPDATE business_users SET last_inbound_at = now() WHERE id=%s", (business_user_id,))
        await emit(c, "owner.message_received", {"owner_message_id": ins["id"], "business_user_id": business_user_id},
                   business_id=bid, ordering_key=f"owner:{business_user_id}")

    # ---------------------------------------------------------------- owner replied from the Business app (echo)
    async def _echo(self, number: dict[str, Any], ev: EchoMessage) -> None:
        bid = number["business_id"]
        version: int | None = None
        conv_id: uuid.UUID | None = None
        async with self.db.tenant(bid) as c:
            cust = await (await c.execute("SELECT id FROM customers WHERE wa_id=%s", (ev.to_id,))).fetchone()
            if cust is None:
                return  # a chat the AI never saw (possibly personal): never ingest it (privacy)
            conv = await (await c.execute(
                "SELECT * FROM conversations WHERE customer_id=%s AND whatsapp_number_id=%s FOR UPDATE", (cust["id"], number["id"]))).fetchone()
            if conv is None:
                return
            conv_id = conv["id"]
            latency = None
            if conv["last_inbound_at"]:
                latency = int((ev.timestamp - conv["last_inbound_at"]).total_seconds() * 1000)
            ins = await (await c.execute(
                """INSERT INTO messages (business_id, conversation_id, wa_message_id, direction, sender, kind, body, payload,
                                         status, gap_ms, answered, wa_timestamp, sent_at)
                   VALUES (%s,%s,%s,'out','owner',%s,%s,%s,'sent',%s,true,%s,%s)
                   ON CONFLICT (wa_message_id) DO NOTHING RETURNING id""",
                (bid, conv_id, ev.channel_message_id, ev.kind, ev.text, _json(ev.raw), latency, ev.timestamp, ev.timestamp))).fetchone()
            if ins is None:
                return
            settings = (await (await c.execute("SELECT conversation_settings FROM businesses WHERE id=%s", (bid,))).fetchone())["conversation_settings"]
            pause = timedelta(minutes=int(settings.get("owner_pause_minutes", 120)))
            upd = await (await c.execute(
                """UPDATE conversations SET version = version + 1, state = 'idle', ai_paused_until = now() + %s,
                          ai_paused_reason = 'owner_reply', last_owner_reply_at = now(), last_outbound_at = now()
                   WHERE id=%s RETURNING version""", (pause, conv_id))).fetchone()
            version = upd["version"]
            await c.execute("UPDATE messages SET answered=true WHERE conversation_id=%s AND direction='in' AND NOT answered", (conv_id,))
        await self.queue.cancel_superseded(f"conversation:{conv_id}", version)

    # ---------------------------------------------------------------- delivery / read receipts
    async def _status(self, number: dict[str, Any], ev: StatusUpdate) -> None:
        bid = number["business_id"]
        async with self.db.tenant(bid) as c:
            msg = await (await c.execute(
                "SELECT id, status, conversation_id, sender FROM messages WHERE wa_message_id=%s", (ev.channel_message_id,))).fetchone()
            if msg is None:
                omsg = await (await c.execute("SELECT id, status FROM owner_messages WHERE wa_message_id=%s", (ev.channel_message_id,))).fetchone()
                if omsg and (ev.status == "failed" or _RANK.get(ev.status, -1) > _RANK.get(omsg["status"], -1)):
                    await c.execute("UPDATE owner_messages SET status=%s, error=%s WHERE id=%s",
                                    (ev.status, ev.error, omsg["id"]))
                return
            if ev.status == "failed":
                await c.execute("UPDATE messages SET status='failed', error=%s WHERE id=%s", (f"{ev.error_code}: {ev.error}", msg["id"]))
                await self._failed_delivery(c, bid, msg, ev)
                return
            if _RANK.get(ev.status, -1) <= _RANK.get(msg["status"], -1):
                return
            col = {"sent": "sent_at", "delivered": "delivered_at", "read": "read_at"}[ev.status]
            await c.execute(f"UPDATE messages SET status=%s, {col}=COALESCE({col}, %s) WHERE id=%s",  # noqa: S608
                            (ev.status, ev.timestamp, msg["id"]))

    async def _failed_delivery(self, c, bid: uuid.UUID, msg: dict[str, Any], ev: StatusUpdate) -> None:  # noqa: ANN001
        """INV-12: a message that Meta reports as failed becomes a handoff + operator alert."""
        import json
        open_h = await (await c.execute(
            "SELECT 1 FROM handoffs WHERE conversation_id=%s AND status='open'", (msg["conversation_id"],))).fetchone()
        if not open_h and msg["sender"] == "ai":
            h = await (await c.execute(
                """INSERT INTO handoffs (business_id, conversation_id, reason, note) VALUES (%s,%s,'system_failure',%s) RETURNING id""",
                (bid, msg["conversation_id"], f"Message delivery failed: {ev.error_code} {ev.error}"))).fetchone()
            await emit(c, "handoff.created", {"handoff_id": h["id"], "conversation_id": msg["conversation_id"], "reason": "system_failure"},
                       business_id=bid, ordering_key=f"biz:{bid}")
        async with self.db.system_tx() as s:
            await s.execute("INSERT INTO operator_alerts (business_id, severity, kind, message, detail) VALUES (%s,'warning','delivery_failed',%s,%s::jsonb)",
                            (bid, f"Delivery failed: {ev.error_code} {ev.error}", json.dumps({"code": ev.error_code, "message_id": ev.channel_message_id})))

    # ---------------------------------------------------------------- account events
    async def _account(self, number: dict[str, Any], ev: AccountEvent) -> None:
        async with self.db.tenant(number["business_id"]) as c:
            await emit(c, "platform.account_update",
                       {"phone_number_id": ev.phone_number_id or "", "kind": ev.kind, "detail": ev.detail},
                       business_id=number["business_id"], ordering_key=f"biz:{number['business_id']}")


def _json(d: dict[str, Any]):
    from salesai.db import jsonb
    return jsonb(d)
