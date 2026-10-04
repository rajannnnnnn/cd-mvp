"""Business hours from the owner's profile. profile.hours = {"mon": "10:00-20:00", ..., "sun": "closed"}
(values may also be lists of ranges). No hours configured = always open."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _ranges(v: Any) -> list[tuple[time, time]]:
    if not v or (isinstance(v, str) and v.strip().lower() in ("closed", "off")):
        return []
    items = [v] if isinstance(v, str) else list(v)
    out = []
    for it in items:
        try:
            a, b = str(it).split("-")
            out.append((time.fromisoformat(a.strip()), time.fromisoformat(b.strip())))
        except ValueError:
            continue
    return out


def structured(profile: dict[str, Any]) -> dict[str, list[tuple[time, time]]] | None:
    h = profile.get("hours")
    if not isinstance(h, dict) or not h:
        return None
    return {k: _ranges(h.get(k)) for k in KEYS}


def is_open(profile: dict[str, Any], tz: str, now: datetime) -> bool:
    h = structured(profile)
    if h is None:
        return True
    local = now.astimezone(ZoneInfo(tz))
    return any(a <= local.time() <= b for a, b in h[KEYS[local.weekday()]])


def next_open(profile: dict[str, Any], tz: str, now: datetime) -> datetime | None:
    h = structured(profile)
    if h is None:
        return now
    z = ZoneInfo(tz)
    local = now.astimezone(z)
    for add in range(0, 8):
        day = (local + timedelta(days=add)).date()
        for a, _ in sorted(h[KEYS[day.weekday()]]):
            cand = datetime.combine(day, a, tzinfo=z)
            if cand > local:
                return cand
    return None
