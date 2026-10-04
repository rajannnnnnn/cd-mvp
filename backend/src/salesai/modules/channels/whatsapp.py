"""WhatsApp Cloud API adapter: webhook parsing and sending.

NOTE: payload shapes follow Meta's public Cloud API documentation. No Meta account exists yet, so the
fixtures in tests/fixtures/meta/ are synthetic, written from the docs — replace them with recorded
payloads from the Meta test number before launch (see docs/PROGRESS.md)."""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from salesai.modules.channels.base import (
    AccountEvent,
    ChannelEvent,
    EchoMessage,
    InboundMessage,
    NumberRef,
    SendResult,
    StatusUpdate,
)

log = logging.getLogger("salesai.channels.whatsapp")

# Meta error codes -> behavior
WINDOW_CLOSED = {"131047", "131051"}            # re-engagement required / unsupported
RATE_LIMITED = {"130429", "131056", "80007", "4", "131048"}   # throughput / pair rate / spam rate
TOKEN_INVALID = {"190", "463", "467", "102"}
RESTRICTED = {"131031", "131053", "368", "131042"}
UNDELIVERABLE = {"131026", "131021", "131049"}


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: 'sha256=<hex hmac of the raw body with the app secret>'."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def sign(app_secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def _ts(v: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(v), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


_KINDS: dict[str, Literal["text", "audio", "image", "reaction"]] = {"text": "text", "audio": "audio", "voice": "audio", "image": "image", "reaction": "reaction"}


def _kind(t: str) -> Literal["text", "audio", "image", "reaction", "other"]:
    return _KINDS.get(t, "other")


def _body(m: dict[str, Any]) -> str | None:
    t = m.get("type")
    if t == "text":
        return (m.get("text") or {}).get("body")
    if t == "image":
        return (m.get("image") or {}).get("caption")
    if t == "button":
        return (m.get("button") or {}).get("text")
    if t == "interactive":
        i = m.get("interactive") or {}
        return ((i.get("button_reply") or i.get("list_reply")) or {}).get("title")
    if t == "reaction":
        return (m.get("reaction") or {}).get("emoji")
    if t == "location":
        loc = m.get("location") or {}
        return f"[location {loc.get('latitude')},{loc.get('longitude')}] {loc.get('name', '')}".strip()
    return None


def parse_webhook(payload: dict[str, Any]) -> list[ChannelEvent]:
    """Normalize a Meta webhook payload into channel-neutral events. Unknown fields are ignored,
    never raised on: Meta adds fields without notice."""
    events: list[ChannelEvent] = []
    if payload.get("object") != "whatsapp_business_account":
        return events
    for entry in payload.get("entry") or []:
        waba = entry.get("id")
        for ch in entry.get("changes") or []:
            field_, v = ch.get("field"), ch.get("value") or {}
            pn = (v.get("metadata") or {}).get("phone_number_id")
            if field_ == "messages":
                names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in v.get("contacts") or []}
                for m in v.get("messages") or []:
                    t = m.get("type", "other")
                    media = (m.get(t) or {}).get("id") if isinstance(m.get(t), dict) else None
                    events.append(InboundMessage(
                        channel_message_id=m["id"], from_id=m.get("from", ""), to_phone_number_id=pn or "",
                        kind=_kind(t), text=_body(m), timestamp=_ts(m.get("timestamp")),
                        sender_name=names.get(m.get("from")), reply_to=(m.get("context") or {}).get("id"),
                        media_id=media if t in ("audio", "voice", "image") else None,
                        reaction_to=(m.get("reaction") or {}).get("message_id"), raw=m))
                for s in v.get("statuses") or []:
                    err = (s.get("errors") or [{}])[0]
                    status = s.get("status")
                    if status in ("sent", "delivered", "read", "failed"):
                        events.append(StatusUpdate(
                            channel_message_id=s["id"], phone_number_id=pn or "", status=status,
                            timestamp=_ts(s.get("timestamp")), recipient_id=s.get("recipient_id"),
                            error_code=str(err["code"]) if err.get("code") is not None else None,
                            error=err.get("title") or err.get("message")))
            elif field_ == "smb_message_echoes":
                for m in v.get("message_echoes") or []:
                    events.append(EchoMessage(
                        channel_message_id=m["id"], from_phone_number_id=pn or "", to_id=m.get("to", ""),
                        kind=_kind(m.get("type", "other")), text=_body(m), timestamp=_ts(m.get("timestamp")), raw=m))
            elif field_ in ("account_update", "phone_number_quality_update", "account_review_update"):
                events.append(_account_event(field_, v, waba, pn))
    return events


def _account_event(field_: str, v: dict[str, Any], waba: str | None, pn: str | None) -> AccountEvent:
    ev = str(v.get("event") or "").upper()
    detail: dict[str, Any] = {"field": field_, "event": ev, **{k: v[k] for k in ("current_limit", "decision", "display_phone_number") if k in v}}
    if field_ == "phone_number_quality_update":
        rating = {"FLAGGED": "yellow", "UNFLAGGED": "green", "DOWNGRADE": "red", "ONBOARDING": "green"}.get(ev)
        if v.get("quality_rating"):
            rating = str(v["quality_rating"]).lower()
        detail["quality_rating"] = rating
        return AccountEvent(pn, waba, "quality", detail)
    if ev in ("PARTNER_REMOVED", "DISABLED_UPDATE", "ACCOUNT_DELETED", "COEXISTENCE_DISCONNECTED"):
        return AccountEvent(pn, waba, "disconnected", detail)
    if ev in ("ACCOUNT_RESTRICTION", "ACCOUNT_VIOLATION", "RESTRICTED"):
        return AccountEvent(pn, waba, "restricted", detail)
    if ev in ("ACCOUNT_RECONNECTED", "RECONNECTED", "VERIFIED_ACCOUNT"):
        return AccountEvent(pn, waba, "reconnected", detail)
    return AccountEvent(pn, waba, "other", detail)


def classify_error(code: str | None, status: int, message: str) -> SendResult:
    code = code or ""
    if code in WINDOW_CLOSED:
        return SendResult(False, error_code=code, error=message, permanent="window_closed")
    if code in TOKEN_INVALID:
        return SendResult(False, error_code=code, error=message, permanent="token_invalid")
    if code in RESTRICTED:
        return SendResult(False, error_code=code, error=message, permanent="restricted")
    if code in UNDELIVERABLE:
        return SendResult(False, error_code=code, error=message, permanent="undeliverable")
    if code in RATE_LIMITED or status == 429:
        return SendResult(False, error_code=code, error=message, retryable=True, retry_after_s=5.0)
    if status >= 500:
        return SendResult(False, error_code=code or str(status), error=message, retryable=True)
    return SendResult(False, error_code=code or str(status), error=message, permanent="other")


class CloudApiChannel:
    name = "whatsapp_cloud"

    def __init__(self, base: str, version: str, timeout_s: float = 10.0, client: httpx.AsyncClient | None = None):
        self.base, self.version = base.rstrip("/"), version
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _url(self, number: NumberRef, path: str = "messages") -> str:
        return f"{self.base}/{self.version}/{number.phone_number_id}/{path}"

    async def _post(self, number: NumberRef, body: dict[str, Any]) -> SendResult:
        if not number.access_token:
            return SendResult(False, error="no access token", permanent="token_invalid")
        try:
            r = await self._client.post(self._url(number), json={"messaging_product": "whatsapp", **body},
                                        headers={"Authorization": f"Bearer {number.access_token}"})
        except httpx.HTTPError as e:
            return SendResult(False, error=f"network: {e}", retryable=True)
        data = r.json() if r.content else {}
        if r.status_code < 300:
            msgs = data.get("messages") or [{}]
            return SendResult(True, message_id=msgs[0].get("id"))
        err = data.get("error") or {}
        code = str(err["code"]) if err.get("code") is not None else None
        return classify_error(code, r.status_code, err.get("message") or r.text[:200])

    async def send_text(self, number: NumberRef, to: str, text: str, reply_to: str | None = None) -> SendResult:
        body: dict[str, Any] = {"to": to, "type": "text", "text": {"body": text, "preview_url": False}}
        if reply_to:
            body["context"] = {"message_id": reply_to}
        return await self._post(number, body)

    async def send_reaction(self, number: NumberRef, to: str, message_id: str, emoji: str) -> SendResult:
        return await self._post(number, {"to": to, "type": "reaction", "reaction": {"message_id": message_id, "emoji": emoji}})

    async def send_template(self, number: NumberRef, to: str, name: str, language: str, params: list[str]) -> SendResult:
        comp = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] if params else []
        return await self._post(number, {"to": to, "type": "template",
                                         "template": {"name": name, "language": {"code": language}, "components": comp}})

    async def mark_read(self, number: NumberRef, message_id: str, typing: bool = True) -> SendResult:
        body: dict[str, Any] = {"status": "read", "message_id": message_id}
        if typing:
            body["typing_indicator"] = {"type": "text"}
        return await self._post(number, body)

    async def download_media(self, number: NumberRef, media_id: str) -> bytes:
        hdr = {"Authorization": f"Bearer {number.access_token}"}
        meta = (await self._client.get(f"{self.base}/{self.version}/{media_id}", headers=hdr)).json()
        return (await self._client.get(meta["url"], headers=hdr)).content
