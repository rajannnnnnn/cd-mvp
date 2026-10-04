"""Conversation state helpers shared by inbound routing, turns and delivery."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

WINDOW = timedelta(hours=24)       # Meta customer service window (CR-2)
WINDOW_SAFETY = timedelta(minutes=2)

OPT_OUT = {"stop", "unsubscribe", "opt out", "optout", "stop messages", "stop all", "remove me",
           "stop messaging", "band karo", "msg band karo", "messages band karo", "mujhe messages mat bhejo"}
OPT_IN = {"start", "subscribe", "resume", "opt in", "optin"}


def norm_keyword(text: str | None) -> str:
    return "".join(ch for ch in (text or "").lower().strip() if ch.isalnum() or ch == " ").strip()


def is_opt_out(text: str | None) -> bool:
    return norm_keyword(text) in OPT_OUT


def is_opt_in(text: str | None) -> bool:
    return norm_keyword(text) in OPT_IN


def window_open(last_inbound_at: datetime | None, now: datetime | None = None) -> bool:
    """INV-8: free-form messages only inside 24h of the customer's last message (with a small safety margin)."""
    if last_inbound_at is None:
        return False
    now = now or datetime.now(UTC)
    return now - last_inbound_at < WINDOW - WINDOW_SAFETY


def window_closes_at(last_inbound_at: datetime | None) -> datetime | None:
    return last_inbound_at + WINDOW if last_inbound_at else None


@dataclass(frozen=True)
class Block:
    reason: str


def ai_block_reason(*, business: dict[str, Any], conv: dict[str, Any], customer: dict[str, Any],
                    number: dict[str, Any] | None, now: datetime | None = None) -> str | None:
    """INV-10 (+ owner controls): the single place that says whether the AI may speak in a conversation."""
    now = now or datetime.now(UTC)
    if customer["is_personal"]:
        return "personal_contact"
    if customer["opted_out"]:
        return "opted_out"
    if not business["ai_enabled"]:
        return "ai_disabled"
    if business["status"] in ("paused", "churned"):
        return "business_inactive"
    if number is not None and number["status"] != "connected":
        return "number_disconnected"
    if conv["ai_paused_until"] is not None and conv["ai_paused_until"] > now:
        return f"paused:{conv['ai_paused_reason'] or 'owner'}"
    return None
