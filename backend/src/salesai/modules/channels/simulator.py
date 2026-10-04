"""Simulated WhatsApp network. It stands in for Meta: it holds what each human's phone would show
(`sim_messages`) and emits genuine, correctly SIGNED Meta-format webhooks into the real ingress.
The product code path under test is identical to production; only the network is simulated."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from salesai.db import Database
from salesai.modules.channels.base import NumberRef, SendResult
from salesai.modules.channels.whatsapp import sign
from salesai.phone import wa_id
from salesai.templates import render


def _wamid() -> str:
    return f"wamid.SIM{uuid.uuid4().hex[:24]}"


class SimulatorChannel:
    name = "simulator"

    def __init__(self, db: Database):
        self.db = db

    async def _record(self, number: NumberRef, to: str, **cols: Any) -> SendResult:
        mid = _wamid()
        async with self.db.system_tx() as c:
            await c.execute(
                """INSERT INTO sim_messages (phone, business_phone, direction, kind, body, template_name,
                                              wa_message_id, reaction_to, typing)
                   VALUES (%s,%s,'to_user',%s,%s,%s,%s,%s,%s)""",
                (to, number.display_phone, cols.get("kind", "text"), cols.get("body"), cols.get("template_name"),
                 mid, cols.get("reaction_to"), cols.get("typing", False)))
        return SendResult(True, message_id=mid)

    async def send_text(self, number: NumberRef, to: str, text: str, reply_to: str | None = None) -> SendResult:
        return await self._record(number, to, kind="text", body=text)

    async def send_reaction(self, number: NumberRef, to: str, message_id: str, emoji: str) -> SendResult:
        return await self._record(number, to, kind="reaction", body=emoji, reaction_to=message_id)

    async def send_template(self, number: NumberRef, to: str, name: str, language: str, params: list[str]) -> SendResult:
        return await self._record(number, to, kind="template", body=render(name, language, params), template_name=name)

    async def mark_read(self, number: NumberRef, message_id: str, typing: bool = True) -> SendResult:
        async with self.db.system_tx() as c:
            row = await (await c.execute(
                "SELECT phone FROM sim_messages WHERE wa_message_id=%s AND direction='from_user'", (message_id,))).fetchone()
            if row:
                await c.execute("UPDATE sim_messages SET status='read' WHERE wa_message_id=%s", (message_id,))
                if typing:
                    await c.execute(
                        "INSERT INTO sim_messages (phone, business_phone, direction, kind, typing, wa_message_id)"
                        " VALUES (%s,%s,'to_user','typing',true,%s)", (row["phone"], number.display_phone, _wamid()))
        return SendResult(True)

    async def download_media(self, number: NumberRef, media_id: str) -> bytes:
        return b""


# --------------------------------------------------------------------------- the simulated network
def build_message_payload(phone_number_id: str, display_phone: str, from_phone: str, text: str | None, *,
                          kind: str = "text", wamid: str | None = None, name: str | None = None,
                          media_id: str | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"from": wa_id(from_phone), "id": wamid or _wamid(), "timestamp": str(int(time.time())), "type": kind}
    if kind == "text":
        msg["text"] = {"body": text or ""}
    elif kind in ("audio", "image"):
        msg[kind] = {"id": media_id or f"media-{uuid.uuid4().hex[:8]}", **({"caption": text} if text and kind == "image" else {})}
    return {"object": "whatsapp_business_account", "entry": [{"id": "sim-waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": wa_id(display_phone), "phone_number_id": phone_number_id},
        "contacts": [{"profile": {"name": name or "Customer"}, "wa_id": wa_id(from_phone)}],
        "messages": [msg]}}]}]}


def build_echo_payload(phone_number_id: str, display_phone: str, to_phone: str, text: str) -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": [{"id": "sim-waba", "changes": [{"field": "smb_message_echoes", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": wa_id(display_phone), "phone_number_id": phone_number_id},
        "message_echoes": [{"from": wa_id(display_phone), "to": wa_id(to_phone), "id": _wamid(),
                            "timestamp": str(int(time.time())), "type": "text", "text": {"body": text}}]}}]}]}


def build_status_payload(phone_number_id: str, display_phone: str, to_phone: str, wamid: str, status: str) -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": [{"id": "sim-waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": wa_id(display_phone), "phone_number_id": phone_number_id},
        "statuses": [{"id": wamid, "status": status, "timestamp": str(int(time.time())), "recipient_id": wa_id(to_phone)}]}}]}]}


def build_account_payload(phone_number_id: str, display_phone: str, field: str, event: str) -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": [{"id": "sim-waba", "changes": [{"field": field, "value": {
        "display_phone_number": wa_id(display_phone), "event": event,
        "metadata": {"phone_number_id": phone_number_id}}}]}]}


def signed(app_secret: str, payload: dict[str, Any]) -> tuple[bytes, str]:
    raw = json.dumps(payload).encode()
    return raw, sign(app_secret, raw)
