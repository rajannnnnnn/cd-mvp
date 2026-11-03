"""The agent's clock as rules (ADR 0038): given what has happened in one conversation, which timers should exist and when are they due.
Pure functions over a small state, no database and no model: this is the "rules" tier, and it is what the plan is computed from every
time something changes, so a timer is always the answer to "what does this chat need now", never a leftover."""
from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

GRACE = timedelta(seconds=30)                 # after a pause ends, give the owner's last message time to settle before the AI steps in
MIN_FOLLOW_BACK_MIN = 30                      # never follow up inside half an hour
RHYTHM_FACTOR = 4                             # wait four times the customer's usual reply time
WINDOW_LIMIT = timedelta(hours=22)            # free-form messages end 24 h after the customer's last message; keep a margin
ENGAGED = ("interested", "negotiating", "ready_to_buy")
STAGE_FACTOR = {"interested": 1.0, "negotiating": 0.75, "ready_to_buy": 0.5}      # the closer to buying, the sooner


@dataclass(frozen=True)
class ConvState:
    now: datetime
    ai_allowed: bool                          # AI on, shop active, number connected, customer not opted out / personal (the pause is separate)
    paused_until: datetime | None
    pause_reason: str | None                  # owner_reply | handoff | manual
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    last_speaker: str | None                  # customer | ai | owner
    waiting_unanswered: bool                  # a customer message the normal path (turn detection) has not answered yet
    waiting_released: bool                    # a customer message after the last reply that was set aside during a pause
    queued_ai: bool                           # an AI reply is planned and not sent yet
    lead_stage: str
    selling_stopped: bool
    follow_back_after: datetime | None        # the customer message a follow-back already fired for
    rhythm_minutes: float | None              # how long this customer usually takes to answer us
    follow_back_minutes: int                  # the shop's own setting: the longest it waits
    hours: dict[str, Any] | None              # the shop's profile (opening hours)
    tz: str
    hours_behavior: str


@dataclass(frozen=True)
class Planned:
    due_at: datetime
    reason: str
    why: str                                  # one plain sentence for the audit: why it is due when it is. No customer content.


def rhythm_of(latencies_s: list[float]) -> float | None:
    """The customer's usual time to answer us, in minutes: the median of their recent waits (under a day)."""
    v = [x for x in latencies_s if 0 < x < 86_400]
    return statistics.median(v) / 60 if len(v) >= 2 else None


def desired(s: ConvState, next_open: Callable[[Any, str, datetime], datetime | None]) -> dict[str, Planned]:
    if not s.ai_allowed:
        return {}
    out: dict[str, Planned] = {}
    paused = s.paused_until is not None and s.paused_until > s.now
    if paused:
        assert s.paused_until is not None
        out["takeover_check"] = Planned(s.paused_until + GRACE, s.pause_reason or "paused",
                                        f"the AI is stepping back ({s.pause_reason or 'paused'}) and checks again when that ends")
        return out
    if s.waiting_released and not s.waiting_unanswered and s.last_speaker == "customer":
        out["takeover_check"] = Planned(s.now, "customer_waiting", "the pause is over and the customer's last message has had no answer from anyone")
        return out
    if (s.last_speaker == "ai" and not s.queued_ai and s.lead_stage in ENGAGED and not s.selling_stopped
            and s.last_inbound_at is not None and s.last_outbound_at is not None and s.follow_back_after != s.last_inbound_at):
        base = float(s.follow_back_minutes)
        note = f"the shop's usual {s.follow_back_minutes} minutes"
        if s.rhythm_minutes is not None:
            fast = max(float(MIN_FOLLOW_BACK_MIN), RHYTHM_FACTOR * s.rhythm_minutes)
            if fast < base:
                base, note = fast, f"four times this customer's usual {round(s.rhythm_minutes)} minutes to answer"
        minutes = max(float(MIN_FOLLOW_BACK_MIN), base * STAGE_FACTOR[s.lead_stage])
        due = s.last_outbound_at + timedelta(minutes=minutes)
        why = f"{note}, scaled for a customer who is {s.lead_stage.replace('_', ' ')}"
        if s.hours_behavior != "reply_normally" and s.hours is not None:
            opening = next_open(s.hours, s.tz, due)
            if opening is not None and opening > due:
                due, why = opening + timedelta(minutes=5), why + ", moved to when the shop opens"
        if due < s.last_inbound_at + WINDOW_LIMIT:
            out["follow_back"] = Planned(due, "ai_replied", why)
    return out
