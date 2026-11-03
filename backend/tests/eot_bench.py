"""Replays a burst through an end-of-turn predictor the way the system schedules it: a check when each message arrives, and a re-check
after whatever wait the predictor asks for (a re-check that a newer message has overtaken is dropped, as the queue drops it, INV-7)."""
from __future__ import annotations

import heapq
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from salesai.modules.conversations.eot import EndOfTurnPredictor, EotInput
from salesai.modules.tenants import defaults
from tests.eot_corpus import Burst

T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


@dataclass
class Outcome:
    burst: str
    fired_ms: int
    included: int
    total: int
    wait_after_last_ms: int | None          # None when it fired before the customer had finished (premature)

    @property
    def premature(self) -> bool:
        return self.included < self.total


async def replay(predictor: EndOfTurnPredictor, burst: Burst, settings: dict[str, Any] | None = None, **extra: Any) -> Outcome:
    settings = {**defaults.CONVERSATION_SETTINGS, **(settings or {})}
    msgs = burst.messages
    checks: list[tuple[int, int, int]] = []                  # (time, tiebreak, version) ; version = messages seen when scheduled
    for i, (t, _, _) in enumerate(msgs):
        heapq.heappush(checks, (t, i, i + 1))                 # a check runs the moment a message lands
    fired_at, included = None, 0
    n = 0
    while checks:
        t, _, version = heapq.heappop(checks)
        arrived = [m for m in msgs if m[0] <= t]
        if version != len(arrived):
            continue                                          # overtaken by a newer message: dropped
        inp = EotInput(texts=[m[1] for m in arrived], kinds=[m[2] for m in arrived], last_message_at=T0 + timedelta(milliseconds=arrived[-1][0]),
                       first_unanswered_at=T0 + timedelta(milliseconds=arrived[0][0]), now=T0 + timedelta(milliseconds=t),
                       gaps_ms=list(burst.history_gaps_ms), settings=settings, ai_asked_question=burst.ai_asked,
                       message_times=[T0 + timedelta(milliseconds=m[0]) for m in arrived], **extra)
        d = await predictor.decide(inp)
        if d.ready:
            fired_at, included = t, len(arrived)
            break
        n += 1
        heapq.heappush(checks, (t + max(d.wait_ms, 1), 10_000 + n, version))
    assert fired_at is not None, f"{burst.name}: never fired"
    last = msgs[-1][0]
    return Outcome(burst.name, fired_at, included, len(msgs), None if included < len(msgs) else fired_at - last)


def summarize(outcomes: list[Outcome]) -> dict[str, float]:
    waits = sorted(o.wait_after_last_ms for o in outcomes if o.wait_after_last_ms is not None)
    return {"bursts": len(outcomes), "premature": sum(o.premature for o in outcomes), "premature_pct": round(100 * sum(o.premature for o in outcomes) / len(outcomes), 1),
            "wait_p50_ms": statistics.median(waits) if waits else 0, "wait_p95_ms": waits[int(0.95 * (len(waits) - 1))] if waits else 0, "wait_mean_ms": round(statistics.mean(waits)) if waits else 0}
