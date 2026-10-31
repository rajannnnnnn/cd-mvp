"""Meta webhook payload builders. Shapes follow Meta's current documentation (docs/WHATSAPP_WEBHOOK_REFERENCE.md in the repo).
Independent of the product: nothing here imports it."""
from __future__ import annotations

import base64
import os
import re
from typing import Any


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def new_wamid() -> str:
    return "wamid." + base64.b64encode(os.urandom(30)).decode().rstrip("=")


def _envelope(waba_id: str, value: dict[str, Any], field: str = "messages") -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": [{"id": waba_id, "changes": [{"value": value, "field": field}]}]}


def _meta(display_phone: str, phone_number_id: str) -> dict[str, Any]:
    return {"messaging_product": "whatsapp", "metadata": {"display_phone_number": digits(display_phone), "phone_number_id": phone_number_id}}


def inbound_message(waba_id: str, display_phone: str, pnid: str, *, from_wa: str, name: str | None, wamid: str, ts: int,
                    kind: str = "text", text: str | None = None, emoji: str | None = None, reaction_to: str | None = None,
                    media_id: str | None = None, mime: str | None = None, caption: str | None = None, reply_to: str | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"from": from_wa, "id": wamid, "timestamp": str(ts), "type": kind}
    if kind == "text":
        msg["text"] = {"body": text or ""}
    elif kind == "reaction":
        msg["reaction"] = {"message_id": reaction_to, **({"emoji": emoji} if emoji else {})}
    elif kind == "audio":
        msg["audio"] = {"mime_type": mime or "audio/ogg; codecs=opus", "sha256": "0" * 64, "id": media_id, "voice": True}
    elif kind == "image":
        msg["image"] = {"mime_type": mime or "image/jpeg", "sha256": "0" * 64, "id": media_id, **({"caption": caption} if caption else {})}
    if reply_to:
        msg["context"] = {"from": digits(display_phone), "id": reply_to}
    value = {**_meta(display_phone, pnid), "contacts": [{"profile": {"name": name or from_wa}, "wa_id": from_wa}], "messages": [msg]}
    return _envelope(waba_id, value)


def status(waba_id: str, display_phone: str, pnid: str, *, wamid: str, state: str, recipient: str, ts: int,
           error: dict[str, Any] | None = None) -> dict[str, Any]:
    st: dict[str, Any] = {"id": wamid, "status": state, "timestamp": str(ts), "recipient_id": recipient}
    if state in ("delivered", "read"):          # v24.0+: pricing arrives with delivered/read, conversation is omitted
        st["pricing"] = {"billable": False, "pricing_model": "PMP", "type": "free_customer_service", "category": "service"}
    if state == "failed" and error:
        st["errors"] = [{"code": error["code"], "title": error["title"], "message": error["title"],
                         "error_data": {"details": error.get("details", error["title"])}, "href": "/documentation/business-messaging/whatsapp/support/error-codes"}]
    return _envelope(waba_id, {**_meta(display_phone, pnid), "statuses": [st]})


def echo(waba_id: str, display_phone: str, pnid: str, *, to_wa: str, wamid: str, ts: int, text: str) -> dict[str, Any]:
    e = {"from": digits(display_phone), "to": to_wa, "id": wamid, "timestamp": str(ts), "type": "text", "text": {"body": text}}
    return _envelope(waba_id, {**_meta(display_phone, pnid), "message_echoes": [e]}, field="smb_message_echoes")


def account_update(waba_id: str, event: str, ts: int, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"event": event, **extra}
    return {"object": "whatsapp_business_account", "entry": [{"id": waba_id, "time": ts, "changes": [{"value": value, "field": "account_update"}]}]}


# Graph API error bodies (https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes)
ERRORS: dict[int, tuple[int, str, str]] = {
    131047: (400, "Re-engagement message", "More than 24 hours have passed since the recipient last replied to the sender number."),
    190: (401, "Access token has expired", "Your access token has expired."),
    130429: (429, "Rate limit hit", "Cloud API message throughput has been reached."),
    131056: (429, "Pair rate limit hit", "Too many messages sent from the sender phone number to the same recipient phone number in a short period of time."),
    131051: (400, "Unsupported message type", "Unsupported message type."),
    131009: (400, "Parameter value is not valid", "One or more parameter values are invalid."),
    131049: (400, "Message undeliverable", "This message was not delivered to maintain healthy ecosystem engagement."),
    131026: (400, "Message undeliverable", "Unable to deliver message. The recipient phone number is not a WhatsApp phone number."),
    131031: (400, "Account has been locked", "The WhatsApp Business Account associated with the app has been restricted or disabled."),
}
ASYNC_FAILURES = {131049, 131026}          # accepted by the API, then reported as a `failed` status webhook


def graph_error(code: int) -> tuple[int, dict[str, Any]]:
    http, title, details = ERRORS.get(code, (400, "Error", f"Simulated error {code}"))
    return http, {"error": {"message": f"(#{code}) {title}", "type": "OAuthException", "code": code,
                            "error_data": {"messaging_product": "whatsapp", "details": details}, "fbtrace_id": "SIM" + new_wamid()[6:20]}}
