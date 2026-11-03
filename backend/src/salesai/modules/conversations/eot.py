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


_ELLIPSIS = re.compile(r"(\.{2,}|…)\s*$")
_TERMINAL = re.compile(r"[.?!।]\s*$|[\U0001F300-\U0001FAFF☀-➿]\s*$")
_CONTINUES = re.compile(
    r"(,|;|:|-)\s*$|\b(and|or|but|because|so|also|aur|ya|lekin|kyunki|toh|to|with|for|the|a|an|my|your|ka|ki|ke|ko|mein|se|par|"
    r"about|of|in|on|at|from|into|if|that|which|jo|wo|woh|ye|yeh|is|are|was|tell me|show me|give me|send me|batao mujhe)\s*$", re.I)
# a sentence that starts by asking for something is rarely the whole request: the colour, the size or the occasion usually follows
_DESIRE = re.compile(r"^(i (want|need|would like|wanna|am looking|'?m looking)|looking for|mujhe|hume|humein|i'd like)\b", re.I)
# the words a sentence ends on, in the way customers write English and Hinglish without punctuation
_FINAL = re.compile(
    r"\b(hai|hain|hu|hoon|chahiye|chahiyein|batao|bataiye|bataye|dikhao|dikhaiye|dikhana|bhejo|bhejiye|dena|dijiye|milega|milegi|milenge|hoga|hogi|honge|"
    r"kya|kaise|kab|kitna|kitne|kitni|kidhar|kahan|kaun|nahi|nahin|haan|ha|theek|thik|accha|achha|please|pls|plz|thanks|thank you|thankyou|ok|okay|"
    r"done|price|rate|available|stock|discount|photo|photos|pics|link|address|location|timing|timings|open|aaya|aayi|chalega|chalegi)\s*$", re.I)
_QUESTION_STEM = re.compile(r"^(can|could|would|will|do|does|is|are|how|what|when|where|which|who|why|kya|kitna|kab|kaise)\b", re.I)
_GREETING = re.compile(r"^(hi+|hello+|hey+|hii+|namaste|namaskar|good (morning|evening|afternoon)|hlo|helo|namaskaram|vanakkam|नमस्ते)[\s!.,]*(sir|madam|ji|bhaiya|bhai|team)?[\s!.]*$", re.I)


def classify(text: str, kind: str, *, ai_asked: bool = False, first: bool = True) -> str:
    """'complete' (answer now), 'open' (clearly more to come), 'partial' (a request that usually gets details) or 'unsure'. A rule over the
    words, not a model: cheap, instant and the same every time. Only the last message of a burst is classified; the quiet time asked for
    depends on the class. `first`: it is the burst's only message so far (a lone one-word message is a greeting or a fragment; a one-word
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
    if _GREETING.match(t):
        return "open"                                       # a bare greeting is usually followed by the real question
    if _TERMINAL.search(t):
        return "complete"
    if _CONTINUES.search(t):
        return "open"
    words = t.split()
    if _FINAL.search(t):
        return "complete"
    if ai_asked and len(words) <= 4:
        return "complete"                                   # "red", "yes", "tomorrow evening": the answer to what we just asked
    if len(words) >= 8:
        return "complete"
    if _DESIRE.match(t) and len(words) <= 6:
        return "partial"
    if (len(words) <= 2 and first) or (_QUESTION_STEM.match(t) and len(words) < 5):
        return "open"
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
            need = min(max(min_c + 600, int(0.9 * gap) if gap else 0), 3500)
        elif cls == "partial":
            need = min(max(min_c + 1400, int(1.1 * gap) if gap else 0), 3500)
        else:
            floor = max(min_i, 4000) if kind == "image" and not (inp.texts[-1] or "").strip() else min_i      # a photo is often followed by its question
            need = min(max(floor, int(1.3 * gap) if gap else 0), min(max_wait, 10_000))
        label = {"complete": "complete", "unsure": "unsure", "partial": "partial", "open": "incomplete"}[cls]
        if waited >= max_wait:
            return EotDecision(True, 0, "max_wait")
        if quiet >= need:
            return EotDecision(True, 0, f"quiet_{label}")
        remaining = max_wait - waited
        return EotDecision(False, max(150, min(need - quiet, remaining)), f"waiting_{label}")
