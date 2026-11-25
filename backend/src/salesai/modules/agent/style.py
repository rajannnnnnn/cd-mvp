"""How this customer writes, worked out by rules from their own messages in this chat (ADR 0041). No model call and no cost, per customer only,
recomputed from the most recent messages so a customer who changes tone is followed. The writer is told how they write so the reply sounds like
the same conversation: their language and script mix, how casual or professional they are, how much they write, whether they use emoji.

Two things this is not: it never changes a fact or a number (those come from the directives), and it never carries one customer's style to
another (a profile is built from one customer's messages and used for that customer's reply)."""
from __future__ import annotations

import re
import statistics
from collections import Counter
from typing import Any

from salesai.modules.agent.checks import script_of

WINDOW = 12                                       # the most recent customer messages that describe how they write now
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
SLANG = frozenset({"u", "ur", "r", "pls", "plz", "bro", "bruh", "yaar", "yar", "bhai", "lol", "lmao", "hlo", "hii", "hiii", "hiiii", "helo", "okk", "k",
                   "thx", "thnx", "ty", "gm", "btao", "wat", "wht", "hw", "abt", "tmrw", "tmr", "coz", "cuz", "gud", "nyc", "omg", "haha", "hehe",
                   "arre", "abe", "chal", "bhaiya", "didi", "boss", "dude", "mate"})
CASUAL_ONLY = frozenset({"bhaiya", "didi", "boss"})     # forms of address: friendly, but not slang by themselves
FORMAL = ("kindly", "madam", "ma'am", "regards", "respected", "dear", "would", "could", "may i", "i would like", "request", "appreciate", "greetings",
          "good morning", "good afternoon", "good evening", "thank you", "sir")
ADDRESS = ("sir", "madam", "maam", "ma'am", "bhai", "bhaiya", "bhaiyya", "didi", "ji", "boss", "anna", "sister", "brother")
LENGTH_WORDS = ((4, "terse"), (8, "short"), (14, "medium"))
MAX_WORDS = {"terse": 20, "short": 35, "medium": 60, "long": 90}


_NO_SPACES = re.compile("[฀-໿က-႟ក-៿぀-ヿ㐀-鿿가-힯]")


def _words(t: str) -> list[str]:
    """Words in any script. Scripts written without spaces (Chinese, Japanese, Thai ...) count two characters as one word so a sentence is not one word."""
    out: list[str] = []
    for tok in t.split():
        core = tok.strip(".,;:!?()[]{}\"'-")
        if not any(ch.isalnum() for ch in core):
            continue
        out.extend([core] * (max(1, len(core) // 2) if _NO_SPACES.search(core) else 1))
    return out


def _script_of_message(m: str) -> str | None:
    """The script most of this message's letters are written in, or None when it has no letters (an emoji says nothing about language)."""
    letters = [c for c in m if c.isalpha()]
    return max(Counter(script_of(c) for c in letters).items(), key=lambda kv: kv[1])[0] if letters else None


def _score(text: str) -> float:
    """0 is as casual as it gets, 1 is formal. A guess from visible habits of writing, not a judgement of the person."""
    t = text.strip()
    low = t.lower()
    words = [w.lower() for w in _words(t)]
    s = 0.5
    slang = sum(1 for w in words if w in SLANG and w not in CASUAL_ONLY)
    s -= min(0.35, 0.15 * slang)
    if EMOJI.search(t):
        s -= 0.12
    if re.search(r"([a-z])\1{2,}", low):
        s -= 0.1                                                    # "heyyy", "pleaseee"
    if t == low and not re.search(r"[.?!,]", t) and len(words) <= 6:
        s -= 0.1                                                    # all lowercase, no punctuation, short: a quick message
    elif len(words) <= 3:
        s -= 0.05
    marks = sum(1 for m in FORMAL if re.search(r"(?<![\w'])" + re.escape(m) + r"(?![\w'])", low))
    s += min(0.4, 0.12 * marks)
    if "please" in words:
        s += 0.06
    if t[:1].isupper() and re.search(r"[.?!]\s*$", t) and len(words) >= 6:
        s += 0.1                                                    # a proper sentence
    return max(0.0, min(1.0, s))


def _label(score: float) -> str:
    for hi, name in ((0.25, "very_casual"), (0.42, "casual"), (0.62, "neutral"), (0.80, "professional")):
        if score <= hi:
            return name
    return "formal"


def style_of(messages: list[str]) -> dict[str, Any]:
    """The profile from a customer's messages, oldest first. Only the most recent `WINDOW` count."""
    msgs = [m for m in messages if m and m.strip()][-WINDOW:]
    if not msgs:
        return {"messages": 0, "scripts": {"latin": 1.0}, "script": "latin", "register": "neutral", "register_score": 0.5,
                "length": "short", "median_words": 0.0, "uses_emoji": False, "emoji_rate": 0.0, "lowercase_only": False, "punctuation": "light", "address_terms": []}
    # which script(s) they write in, from the letters themselves. The language is not guessed here: the model reads the messages and answers in it.
    counts = Counter(sc for sc in (_script_of_message(m) for m in msgs) if sc)
    total = sum(counts.values()) or 1
    share = {k: round(v / total, 2) for k, v in counts.most_common()} or {"latin": 1.0}
    # recent messages weigh more: the way they write now matters more than how they began
    weights = [1 + i / len(msgs) for i in range(len(msgs))]
    score = sum(w * _score(m) for w, m in zip(weights, msgs, strict=True)) / sum(weights)
    words = [len(_words(m)) for m in msgs]
    med = statistics.median(words)
    length = next((name for hi, name in LENGTH_WORDS if med <= hi), "long")
    emoji = sum(1 for m in msgs if EMOJI.search(m))
    letters_msgs = [m for m in msgs if any(ch.isalpha() for ch in m)]
    lowercase = bool(letters_msgs) and all(m == m.lower() for m in letters_msgs)
    punct = sum(1 for m in msgs if re.search(r"[.?!,]", m))
    proper = sum(1 for m in letters_msgs if m[:1].isupper() and re.search(r"[.?!]\s*$", m))
    punctuation = "none" if punct == 0 else ("proper" if letters_msgs and proper >= 0.6 * len(letters_msgs) else "light")
    called = [a for a in ADDRESS if any(re.search(r"(?<![\w'])" + re.escape(a) + r"(?![\w'])", m.lower()) for m in msgs)]
    return {"messages": len(msgs), "scripts": share, "script": next(iter(share)), "register": _label(score), "register_score": round(score, 2),
            "length": length, "median_words": float(med), "uses_emoji": emoji > 0, "emoji_rate": round(emoji / len(msgs), 2), "lowercase_only": lowercase,
            "punctuation": punctuation, "address_terms": called}


def reply_hint(style: dict[str, Any]) -> dict[str, Any]:
    """What the writer is asked to match: about as long as the customer writes, one message, an emoji only for someone who uses them."""
    return {"max_words": MAX_WORDS.get(style.get("length", "short"), 35), "messages": 1, "emoji": "one_is_fine" if style.get("uses_emoji") else "none"}
