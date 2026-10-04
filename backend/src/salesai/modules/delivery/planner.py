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


def plan_delivery(parts: list[str], reaction: str | None, timing: dict[str, Any], *, rng: random.Random | None = None,
                  multiplier: float = 1.0, offset_ms: int = 0) -> list[PlannedAction]:
    rng = rng or random.Random()  # noqa: S311
    cap = int(timing["max_total_delay_ms"])
    t = sample(timing["read_delay"], rng, multiplier)
    acts: list[PlannedAction] = [PlannedAction(round(t), "mark_read")]
    if reaction:
        acts.append(PlannedAction(round(t + 250), "send_reaction", emoji=reaction))
        t += 450
    for i, part in enumerate(parts):
        if i > 0:
            t += sample(timing["part_gap"], rng, multiplier)
            acts.append(PlannedAction(round(t), "typing"))
        t += len(part) * sample(timing["typing_ms_per_char"], rng, multiplier)
        acts.append(PlannedAction(round(t), "send_text", text=part, part_index=i, last_part=i == len(parts) - 1))
    # cap the TOTAL delay by compressing the schedule proportionally (order preserved)
    total = t
    ceiling = max(1, cap - offset_ms) if offset_ms < cap else cap
    if total > ceiling:
        f = ceiling / total
        acts = [PlannedAction(max(0, math.floor(a.delay_ms * f)), a.action, a.text, a.emoji, a.part_index, a.last_part) for a in acts]
    return [PlannedAction(a.delay_ms + offset_ms, a.action, a.text, a.emoji, a.part_index, a.last_part) for a in acts]
