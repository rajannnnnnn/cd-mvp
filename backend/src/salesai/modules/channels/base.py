"""Messaging channel abstraction. Conversation and sales logic never see WhatsApp-specific types;
WhatsApp Cloud API and the simulator are adapters behind this interface (Built for change)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class NumberRef:
    """A business's sending identity on some channel. access_token is decrypted just-in-time."""
    id: uuid.UUID
    business_id: uuid.UUID
    channel: str
    phone_number_id: str
    display_phone: str
    access_token: str | None = field(default=None, repr=False)   # never logged


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str | None = None
    error_code: str | None = None
    error: str | None = None
    retryable: bool = False
    retry_after_s: float | None = None
    permanent: Literal["window_closed", "restricted", "token_invalid", "undeliverable", "other"] | None = None


# ---- inbound events (channel-neutral)
@dataclass(frozen=True)
class InboundMessage:
    channel_message_id: str
    from_id: str                    # opaque sender id (WhatsApp: phone digits)
    to_phone_number_id: str
    kind: Literal["text", "audio", "image", "reaction", "other"]
    text: str | None
    timestamp: datetime
    sender_name: str | None = None
    reply_to: str | None = None
    media_id: str | None = None
    reaction_to: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EchoMessage:
    """The owner replied from the WhatsApp Business app (coexistence echo)."""
    channel_message_id: str
    from_phone_number_id: str
    to_id: str
    kind: Literal["text", "audio", "image", "reaction", "other"]
    text: str | None
    timestamp: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StatusUpdate:
    channel_message_id: str
    phone_number_id: str
    status: Literal["sent", "delivered", "read", "failed"]
    timestamp: datetime
    recipient_id: str | None = None
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AccountEvent:
    phone_number_id: str | None
    waba_id: str | None
    kind: Literal["quality", "disconnected", "restricted", "reconnected", "other"]
    detail: dict[str, Any] = field(default_factory=dict)


ChannelEvent = InboundMessage | EchoMessage | StatusUpdate | AccountEvent

# The gateway hands these to the agent through a mailbox (ADR 0025): serialised to plain JSON on one side, rebuilt on the other.
_EVENT_TYPES: dict[str, type] = {"inbound": InboundMessage, "echo": EchoMessage, "status": StatusUpdate, "account": AccountEvent}
_KIND_OF: dict[type, str] = {v: k for k, v in _EVENT_TYPES.items()}


def event_to_dict(ev: ChannelEvent) -> dict[str, Any]:
    out: dict[str, Any] = {"type": _KIND_OF[type(ev)], "data": {}}
    for f in fields(ev):
        v = getattr(ev, f.name)
        out["data"][f.name] = v.isoformat() if isinstance(v, datetime) else v
    return out


def event_from_dict(d: dict[str, Any]) -> ChannelEvent:
    cls = _EVENT_TYPES.get(str(d.get("type")))
    if cls is None:
        raise ValueError(f"unknown channel event type {d.get('type')!r}")
    data = dict(d["data"])
    if "timestamp" in data and isinstance(data["timestamp"], str):
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
    return cls(**data)  # type: ignore[no-any-return]


class MessagingChannel(Protocol):
    name: str

    async def send_text(self, number: NumberRef, to: str, text: str, reply_to: str | None = None) -> SendResult: ...
    async def send_reaction(self, number: NumberRef, to: str, message_id: str, emoji: str) -> SendResult: ...
    async def send_template(self, number: NumberRef, to: str, name: str, language: str,
                            params: list[str]) -> SendResult: ...
    async def mark_read(self, number: NumberRef, message_id: str, typing: bool = True) -> SendResult: ...
    async def download_media(self, number: NumberRef, media_id: str) -> bytes: ...
