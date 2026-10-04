"""End-of-turn prediction (Technical Design: Conversation engine). Decides whether the customer has finished
writing and, if not, when to look again. Sits behind an interface so a small model or a learned predictor can
replace the v1 heuristic. Thresholds come from per-business settings (never constants in code)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class EotInput:
    texts: list[str]                 # unanswered customer messages, oldest first
    kinds: list[str]
    last_message_at: datetime
    first_unanswered_at: datetime
    now: datetime
    gaps_ms: list[int]               # this customer's historical gaps between consecutive messages
    settings: dict[str, Any]


@dataclass(frozen=True)
class EotDecision:
    ready: bool
    wait_ms: int
    reason: str


class EndOfTurnPredictor(Protocol):
    async def decide(self, inp: EotInput) -> EotDecision: ...


_TERMINAL = re.compile(r"[.?!।।…]\s*$|[\U0001F300-\U0001FAFF☀-➿]\s*$")
_CONTINUES = re.compile(r"(,|;|:|-|\.\.\.|…)\s*$|\b(and|or|but|because|so|also|aur|ya|lekin|kyunki|toh|to|with|for|the|a|an|my|your|ka|ki|ke|ko|me|mein|se|par)\s*$", re.I)
_GREETING = re.compile(r"^(hi+|hello+|hey+|hii+|namaste|namaskar|good (morning|evening|afternoon)|hlo|helo|नमस्ते)[\s!.]*$", re.I)


def looks_complete(text: str, kind: str) -> bool:
    t = (text or "").strip()
    if kind in ("audio", "image"):
        return True
    if not t:
        return False
    if _GREETING.match(t):
        return False                       # a bare greeting is usually followed by the real question
    if _TERMINAL.search(t):
        return True
    if _CONTINUES.search(t):
        return False
    return len(t.split()) >= 5


def p75(vals: list[int], default: int) -> int:
    v = sorted(x for x in vals if 0 < x < 120_000)
    return v[int(0.75 * (len(v) - 1))] if v else default


class HeuristicEOT:
    """v1: completeness of the last message + this customer's own typing rhythm. Always bounded by max_wait."""

    async def decide(self, inp: EotInput) -> EotDecision:
        s = inp.settings
        max_wait = int(s.get("max_wait_ms", 12000))
        waited = int((inp.now - inp.first_unanswered_at).total_seconds() * 1000)
        quiet = int((inp.now - inp.last_message_at).total_seconds() * 1000)
        complete = looks_complete(inp.texts[-1] if inp.texts else "", inp.kinds[-1] if inp.kinds else "text")
        gap = p75(inp.gaps_ms, int(s.get("default_gap_ms", 4000)))
        if complete:
            need = max(int(s.get("min_quiet_complete_ms", 1200)), int(0.4 * gap))
            need = min(need, 4000)
        else:
            need = max(int(s.get("min_quiet_incomplete_ms", 3500)), int(1.3 * gap))
            need = min(need, max_wait // 2)
        if waited >= max_wait:
            return EotDecision(True, 0, "max_wait")
        if quiet >= need:
            return EotDecision(True, 0, "quiet_complete" if complete else "quiet_incomplete")
        remaining = max_wait - waited
        return EotDecision(False, max(150, min(need - quiet, remaining)), "waiting_complete" if complete else "waiting_incomplete")
