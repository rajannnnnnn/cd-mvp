"""Delivery planner (Technical Design: Human-like delivery). Turns a reply into TIMED actions: read receipt +
typing indicator, then each part after a typing time and gap. All timing comes from per-business parameters
stored in the database (log-normal priors, replaceable as owner data accumulates) — never constants in code —
and total delay is capped so pacing never costs a sale."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlannedAction:
    delay_ms: int
    action: str                 # mark_read | typing | send_text | send_reaction
    text: str | None = None
    emoji: str | None = None
    part_index: int | None = None
    last_part: bool = False


def sample(params: dict[str, Any], rng: random.Random, scale: float = 1.0) -> float:
    """Log-normal draw clamped to [min_ms, max_ms]."""
    v = rng.lognormvariate(float(params["mu"]), float(params["sigma"])) * scale
    return min(float(params["max_ms"]), max(float(params["min_ms"]), v))


BEAT_MS = 400                   # when the model took longer than a believable typing time, the reply still goes out a beat after it is ready
KEEP_ALIVE_MS = 20_000          # WhatsApp ends the typing indicator after about 25 s; refresh it before that


def plan_delivery(parts: list[str], reaction: str | None, timing: dict[str, Any], *, rng: random.Random | None = None,
                  multiplier: float = 1.0, offset_ms: int = 0, include_read: bool = True, elapsed_ms: int | None = None) -> list[PlannedAction]:
    """`include_read=False`: the read receipt already went out when the customer stopped writing. `elapsed_ms`: the customer has been
    seeing "typing..." for that long already (it started when the AI decided to reply, before the model wrote the words), so that time
    counts towards the typing time instead of being added to it. None means the indicator is not showing yet and the plan starts it."""
    rng = rng or random.Random()  # noqa: S311
    cap = int(timing["max_total_delay_ms"])
    t = sample(timing["read_delay"], rng, multiplier) if include_read else 0.0
    acts: list[PlannedAction] = [PlannedAction(round(t), "mark_read")] if include_read else []
    if reaction:
        acts.append(PlannedAction(round(t + 250), "send_reaction", emoji=reaction))
        t += 450
    keepalive: list[PlannedAction] = []
    for i, part in enumerate(parts):
        credit = 0
        if i > 0:
            t += sample(timing["part_gap"], rng, multiplier)
            acts.append(PlannedAction(round(t), "typing"))
        elif elapsed_ms is None:
            acts.append(PlannedAction(round(t), "typing"))             # the AI is going to reply and nothing is showing yet: start the indicator
        else:
            credit = elapsed_ms                                         # already typing for this long, while the words were being written
        typing_ms = len(part) * sample(timing["typing_ms_per_char"], rng, multiplier)
        started = t - credit
        t += typing_ms if credit <= 0 else max(BEAT_MS, typing_ms - credit)
        for k in range(1, int(max(0.0, t - started) // KEEP_ALIVE_MS) + 1):
            at = started + k * KEEP_ALIVE_MS
            if 0 < at < t:
                keepalive.append(PlannedAction(round(at), "typing", part_index=i))
        acts.append(PlannedAction(round(t), "send_text", text=part, part_index=i, last_part=i == len(parts) - 1))
    # cap the TOTAL delay by compressing the schedule proportionally (order preserved)
    total = t
    ceiling = max(1, cap - offset_ms) if offset_ms < cap else cap
    if total > ceiling:
        f = ceiling / total
        acts = [PlannedAction(max(0, math.floor(a.delay_ms * f)), a.action, a.text, a.emoji, a.part_index, a.last_part) for a in acts]
    else:
        acts = sorted([*acts, *keepalive], key=lambda a: a.delay_ms)   # a compressed plan is short enough not to need refreshing
    return [PlannedAction(a.delay_ms + offset_ms, a.action, a.text, a.emoji, a.part_index, a.last_part) for a in acts]
