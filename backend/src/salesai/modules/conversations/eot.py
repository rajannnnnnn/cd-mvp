"""End-of-turn prediction (Technical Design: Conversation engine). Decides whether the customer has finished
writing and, if not, when to look again. Sits behind an interface so a small model or a learned predictor can
replace the v1 heuristic. Thresholds come from per-business settings (never constants in code)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    ai_asked_question: bool = False  # the AI's last message ended with a question, so a short reply is an answer, not a fragment
    message_times: list[datetime] = field(default_factory=list)   # when each unanswered message arrived (this burst's own rhythm)


@dataclass(frozen=True)
class EotDecision:
    ready: bool
    wait_ms: int
    reason: str


class EndOfTurnPredictor(Protocol):
    async def decide(self, inp: EotInput) -> EotDecision: ...


# Language-neutral on purpose (founder, 2026-11-24): customers write in every Indian language, and no list of words in the code can know them all.
# Only signals that mean the same in every language are used: the message kind, how it ends (punctuation of any script), how long it is, whether
# the AI just asked something, and how this customer types. Reading the meaning of a half-finished message is the model's job, not a rule's.
_ELLIPSIS = re.compile(r"(\.{2,}|…)\s*$")
# a sentence end in any script: . ? ! | Devanagari danda | Arabic/Urdu full stop and question mark | CJK full stops | Armenian | Ethiopic, or a closing emoji
_TERMINAL = re.compile(r"[.?!।॥۔؟。？！．‼‽։።፧។]\s*$|[🌀-🫿☀-➿]\s*$")
# a trailing mark that says "more follows": comma, semicolon, colon, dash, in Latin, Arabic and CJK forms
_CONTINUES = re.compile(r"[,;:\-–—،؛、，；：]\s*$")
# scripts written without spaces between words: a "word" count would call a whole sentence one word, so count by characters instead
_NO_SPACES = re.compile(r"[฀-໿က-႟ក-៿぀-ヿ㐀-鿿가-힯]")
UNSURE_EXTRA = 1.25   # no word list can say a short unpunctuated message is finished, so it waits this much longer than a certain one, as a share of the shop's own quiet time (1.25 x 1200 ms = the 1500 ms measured in ADR 0051)
COMPLETE_UNITS = 8                                          # a message this long with no punctuation is usually a whole thought


def units(text: str) -> int:
    """How many words the message holds, counting two characters as one word in scripts that do not separate words."""
    return sum(max(1, len(tok) // 2) if _NO_SPACES.search(tok) else 1 for tok in text.split())


def classify(text: str, kind: str, *, ai_asked: bool = False, first: bool = True) -> str:
    """'complete' (answer now), 'open' (clearly more to come) or 'unsure'. A rule over punctuation and length, not a model and not a word list:
    cheap, instant, the same every time and the same in every language. Only the last message of a burst is classified; the quiet time asked for
    depends on the class. `first`: it is the burst's only message so far (a lone one- or two-word message is a greeting or a fragment; a short
    addition to an earlier message is not)."""
    t = (text or "").strip()
    if kind == "audio":
        return "complete"                                   # a voice note is a whole thought
    if kind == "image":
        return "open" if not t else classify(t, "text", ai_asked=ai_asked)
    if not t:
        return "open"
    if _ELLIPSIS.search(t):
        return "open"                                       # "so I was thinking..." is the start of something
    if _TERMINAL.search(t):
        return "complete"
    if _CONTINUES.search(t):
        return "open"
    n = units(t)
    if ai_asked and n <= 4:
        return "complete"                                   # a short reply to the question we just asked is the answer, not a fragment
    if n >= COMPLETE_UNITS:
        return "complete"
    if n <= 2 and first:
        return "open"                                       # a lone short message is a greeting or the start of something
    return "unsure"


def looks_complete(text: str, kind: str) -> bool:
    return classify(text, kind) == "complete"


def _p75(vals: list[int]) -> int | None:
    v = sorted(x for x in vals if 0 < x < 120_000)
    return v[int(0.75 * (len(v) - 1))] if v else None


def p75(vals: list[int], default: int) -> int:
    return _p75(vals) or default


def _burst_gap_ms(times: list[datetime]) -> int | None:
    """How fast this customer is writing right now: the median gap between the messages of this burst."""
    gaps = sorted(int((b - a).total_seconds() * 1000) for a, b in zip(times, times[1:], strict=False))
    return gaps[len(gaps) // 2] if gaps else None


class HeuristicEOT:
    """Rules over the last message, the customer's own rhythm (this burst's gaps first, their history second) and what the AI just asked.
    Always bounded by max_wait. Thresholds are per-shop settings. Measured against the labelled corpus in tests/eot_corpus.py (ADR 0036)."""

    async def decide(self, inp: EotInput) -> EotDecision:
        s = inp.settings
        max_wait = int(s.get("max_wait_ms", 12000))
        min_c, min_i = int(s.get("min_quiet_complete_ms", 1200)), int(s.get("min_quiet_incomplete_ms", 3500))
        waited = int((inp.now - inp.first_unanswered_at).total_seconds() * 1000)
        quiet = int((inp.now - inp.last_message_at).total_seconds() * 1000)
        kind = inp.kinds[-1] if inp.kinds else "text"
        cls = classify(inp.texts[-1] if inp.texts else "", kind, ai_asked=inp.ai_asked_question, first=len(inp.texts) <= 1)
        burst = _burst_gap_ms(inp.message_times)
        gap = burst if burst is not None else _p75(inp.gaps_ms)
        rapid = len(inp.message_times) >= 3 and burst is not None and burst < 1500           # firing off short messages one after another
        if cls == "complete":
            need = min(max(min_c, int(0.4 * gap) if gap else 0, 1500 if rapid else 0), 3000)
        elif cls == "unsure":
            need = min(max(int(min_c * (1 + UNSURE_EXTRA)), int(0.9 * gap) if gap else 0), 3500)
        else:
            floor = max(min_i, 4000) if kind == "image" and not (inp.texts[-1] or "").strip() else min_i      # a photo is often followed by its question
            need = min(max(floor, int(1.3 * gap) if gap else 0), min(max_wait, 10_000))
        label = {"complete": "complete", "unsure": "unsure", "open": "incomplete"}[cls]
        if waited >= max_wait:
            return EotDecision(True, 0, "max_wait")
        if quiet >= need:
            return EotDecision(True, 0, f"quiet_{label}")
        remaining = max_wait - waited
        return EotDecision(False, max(150, min(need - quiet, remaining)), f"waiting_{label}")
