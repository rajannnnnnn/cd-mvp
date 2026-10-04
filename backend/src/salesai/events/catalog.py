"""Domain event catalog. Components announce facts; subscribers are declared here, so a feature
can subscribe to an existing event without touching the producer (Built for change).

Envelope (outbox row -> job payload under "_event"): id (dedupe), type, schema_version,
business_id, ordering_key, occurred_at. schema_version lets producers and consumers deploy
independently: consumers must accept the current and previous schema_version."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebhookReceived(_P):
    webhook_event_id: int


class MessageReceived(_P):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    version: int
    rechecks: int = 0                      # end-of-turn re-checks so far for this version


class NudgeDue(_P):
    conversation_id: uuid.UUID
    version: int


class OutboundActionRequested(_P):
    """One timed action produced by the delivery planner."""
    conversation_id: uuid.UUID
    turn_id: uuid.UUID | None = None
    action: str  # mark_read | typing | send_text | send_reaction
    message_id: uuid.UUID | None = None   # outbound row for send_text / reaction
    version: int
    inbound_wa_id: str | None = None      # for mark_read / reaction target
    emoji: str | None = None
    allow_while_paused: bool = False
    last_part: bool = False
    human: bool = False                   # a person (owner/staff) wrote this; AI-specific rules do not apply


class TurnDecided(_P):
    turn_id: uuid.UUID
    conversation_id: uuid.UUID
    action: str
    intent: str | None = None


class HandoffCreated(_P):
    handoff_id: uuid.UUID
    conversation_id: uuid.UUID
    reason: str


class DealCaptured(_P):
    deal_id: uuid.UUID
    conversation_id: uuid.UUID
    kind: str


class KnowledgeGapLogged(_P):
    gap_id: uuid.UUID


class OwnerMessageReceived(_P):
    owner_message_id: uuid.UUID
    business_user_id: uuid.UUID


class OwnerNotificationRequested(_P):
    purpose: str   # handoff_alert | deal_alert | daily_summary | disconnect_alert | setup | otp
    business_user_id: uuid.UUID | None = None   # None = all notify-enabled users
    data: dict[str, Any] = {}


class AccountUpdate(_P):
    phone_number_id: str
    kind: str  # quality | disconnected | restricted | reconnected
    detail: dict[str, Any] = {}


class NumberStatusChanged(_P):
    whatsapp_number_id: uuid.UUID
    status: str


@dataclass(frozen=True)
class Subscription:
    queue: str
    kind: str


@dataclass(frozen=True)
class EventSpec:
    name: str
    version: int
    model: type[BaseModel]
    subscribers: tuple[Subscription, ...] = ()


def _e(name: str, model: type[BaseModel], *subs: tuple[str, str], version: int = 1) -> EventSpec:
    return EventSpec(name, version, model, tuple(Subscription(q, k) for q, k in subs))


CATALOG: dict[str, EventSpec] = {s.name: s for s in [
    _e("webhook.received", WebhookReceived, ("inbound.events", "route_webhook")),
    _e("message.received", MessageReceived, ("conversation.turns", "eot_check")),
    _e("conversation.eot_recheck", MessageReceived, ("conversation.turns", "eot_check")),
    _e("conversation.nudge_due", NudgeDue, ("conversation.turns", "nudge")),
    _e("outbound.action_requested", OutboundActionRequested, ("outbound.actions", "execute")),
    _e("turn.decided", TurnDecided),                        # seam: learning / reporting subscribe here
    _e("handoff.created", HandoffCreated, ("owner.notifications", "notify")),
    _e("deal.captured", DealCaptured, ("owner.notifications", "notify")),
    _e("knowledge_gap.logged", KnowledgeGapLogged),
    _e("owner.message_received", OwnerMessageReceived, ("owner.notifications", "owner_chat")),
    _e("owner.notification_requested", OwnerNotificationRequested, ("owner.notifications", "notify")),
    _e("platform.account_update", AccountUpdate, ("platform.events", "account_update")),
    _e("number.status_changed", NumberStatusChanged, ("platform.events", "number_status")),
]}
