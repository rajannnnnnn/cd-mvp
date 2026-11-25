"""ADR 0036: turn detection is measured. The labelled corpus (tests/eot_corpus.py) is replayed through the v1 heuristic and the current
rules; the current rules must start the AI before the customer has finished far less often, make the customer wait less after they have
finished, and never wait beyond the shop's maximum. The corpus is hand-written and small: this is a regression gate, not a claim about
production accuracy (the live premature-run rate on the founder's audit board is the real measure)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from salesai.modules.conversations.eot import EotInput, HeuristicEOT, classify
from tests.eot_bench import replay, summarize
from tests.eot_corpus import CORPUS, Burst
from tests.eot_v1 import HeuristicEOTV1

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
SETTINGS = {"max_wait_ms": 12000, "min_quiet_complete_ms": 1200, "min_quiet_incomplete_ms": 3500, "default_gap_ms": 4000}


async def measure(predictor) -> tuple[dict, list]:
    out = [await replay(predictor, b) for b in CORPUS]
    return summarize(out), out


async def test_the_current_rules_beat_the_first_heuristic_on_the_corpus():
    v1, _ = await measure(HeuristicEOTV1())
    v2, out = await measure(HeuristicEOT())
    assert v2["premature"] < v1["premature"]                         # fewer starts before the customer finished (wasted or stale answers)
    assert v2["wait_mean_ms"] < v1["wait_mean_ms"] * 0.7             # and the customer waits far less once they have
    assert v2["wait_p95_ms"] < v1["wait_p95_ms"]
    # the gates the rules are held to, with the measured values recorded in ADR 0036
    assert v2["premature_pct"] <= 6.0, v2
    # language-neutral rules (2026-11-24): no word lists, so a short message without punctuation waits longer than a certain one (ADR 0036 amendment)
    assert v2["wait_p50_ms"] <= 3000 and v2["wait_p95_ms"] <= 3500 and v2["wait_mean_ms"] <= 2300, v2
    # what it still gets wrong is known: a voice note followed by a text, and slow typists whose own pause is longer than any wait we allow
    assert {o.burst for o in out if o.premature} <= {"voice-then-text", "slow-typist-1", "slow-typist-2"}


async def test_it_never_waits_past_the_shops_maximum_and_never_answers_inside_the_minimum_quiet():
    p = HeuristicEOT()
    for b in CORPUS:
        o = await replay(p, b)
        last = b.messages[-1][0]
        if not o.premature:
            assert o.wait_after_last_ms is not None and o.wait_after_last_ms <= 12000, b.name
            assert o.wait_after_last_ms >= 1200 or o.fired_ms - b.messages[0][0] >= 12000, b.name        # never inside the shop's minimum quiet time
        assert o.fired_ms <= last + 12000


async def test_a_shop_can_change_the_waits():
    b = next(x for x in CORPUS if x.name == "en-price")
    slow = await replay(HeuristicEOT(), b, {"min_quiet_complete_ms": 3000})
    fast = await replay(HeuristicEOT(), b, {"min_quiet_complete_ms": 500})
    assert fast.wait_after_last_ms < slow.wait_after_last_ms == 3000


# ---------------------------------------------------------------- the rules over a single message

@pytest.mark.parametrize("text,expect", [
    # a sentence end in any script is a finished thought
    ("How much is the red silk saree?", "complete"), ("price?", "complete"), ("Thanks a lot!", "complete"), ("I will come at 6.", "complete"),
    ("इस साड़ी की कीमत क्या है।", "complete"), ("இந்த புடவையின் விலை என்ன?", "complete"), ("এই শাড়ির দাম কত?", "complete"),
    ("كم سعر هذا الفستان؟", "complete"), ("价格多少？", "complete"), ("قیمت کیا ہے۔", "complete"),
    # long enough without punctuation, in any language, including one written without spaces
    ("I saw your post on Instagram and want to know if the blue lehenga is still available", "complete"),
    ("मैंने आपकी पोस्ट देखी और जानना चाहता हूँ कि नीला लहंगा अभी उपलब्ध है या नहीं", "complete"), ("我想买一件红色的丝绸纱丽给我妹妹的婚礼", "complete"),
    # a trailing mark that says more follows, or an ellipsis
    ("so I was thinking...", "open"), ("blue one,", "open"), ("நீல நிறம்،", "open"), ("मुझे दो चाहिए,", "open"),
    # a lone short message is a greeting or a start, in any language
    ("hi", "open"), ("namaste", "open"), ("नमस्ते", "open"), ("வணக்கம்", "open"), ("saree", "open"), ("price batao", "open"),
    # in between: no mark and not long. No list of words can settle it, so it gets a longer quiet time (see the decision tests)
    ("do you have silk sarees", "unsure"), ("ye saree ka price kya hai", "unsure"), ("இந்த புடவை எவ்வளவு", "unsure"), ("ही साडी किती रुपयांना आहे", "unsure"),
])
def test_a_single_message_is_classified(text, expect):
    assert classify(text, "text") == expect


def test_the_same_shape_in_any_language_gets_the_same_class():
    """Rules over punctuation, length and kind only: swapping the words for another language's must not change the class."""
    same = ["ye saree ka price kya hai", "yeh pattu cheelai vilai enna aachu", "ee saree enta dhara andi", "yi saree koto taka bolun"]
    assert len({classify(t, "text") for t in same}) == 1


def test_the_end_of_turn_rules_contain_no_word_lists():
    """Founder, 2026-11-24: the model reads the languages, not the code. A word of any language in the rules is a failure of this test."""
    import re
    from pathlib import Path
    src = (Path(__file__).parents[1] / "src/salesai/modules/conversations/eot.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    for word in ("hai", "kya", "batao", "chahiye", "kitna", "please", "thanks", "namaste", "hello", "because", "lekin", "aur"):
        assert not re.search(r"\b" + word + r"\b", code, re.I), word


def test_a_short_reply_to_a_question_the_ai_just_asked_is_an_answer_not_a_fragment():
    for t in ("red", "yes", "2", "Pune", "tomorrow evening", "haan chalega", "ఎరుపు", "சிவப்பு"):
        assert classify(t, "text", ai_asked=True) == "complete"
    assert classify("red", "text", ai_asked=False) == "open"          # without the question, the same word may be a fragment


def test_photos_and_voice_notes():
    assert classify("", "image") == "open"                                # a photo alone usually has its question coming
    assert classify("", "audio") == "complete"                            # a voice note is a whole thought
    assert classify("is this available?", "image") == "complete"          # a caption is judged like any text
    assert classify("ఇది అందుబాటులో ఉందా?", "image") == "complete"


def test_a_one_word_addition_to_an_earlier_message_is_not_treated_like_a_lone_fragment():
    assert classify("dupatta", "text", first=True) == "open"
    assert classify("one dupatta", "text", first=False) == "unsure"


# ---------------------------------------------------------------- the decision

def inp(texts, kinds=None, *, quiet_ms=0, waited_ms=None, times_ms=None, history=(), ai_asked=False, settings=None):
    kinds = kinds or ["text"] * len(texts)
    last = NOW - timedelta(milliseconds=quiet_ms)
    waited_ms = quiet_ms if waited_ms is None else waited_ms
    times = [NOW - timedelta(milliseconds=t) for t in (times_ms or [quiet_ms])]
    return EotInput(texts=list(texts), kinds=kinds, last_message_at=last, first_unanswered_at=NOW - timedelta(milliseconds=waited_ms), now=NOW,
                    gaps_ms=list(history), settings={**SETTINGS, **(settings or {})}, ai_asked_question=ai_asked, message_times=times)


async def test_a_complete_message_is_answered_after_the_minimum_quiet_and_not_before():
    p = HeuristicEOT()
    assert not (await p.decide(inp(["How much is it?"], quiet_ms=1000))).ready
    d = await p.decide(inp(["How much is it?"], quiet_ms=1200))
    assert d.ready and d.reason == "quiet_complete"


async def test_it_waits_for_the_remaining_time_not_a_fixed_pause():
    d = await HeuristicEOT().decide(inp(["How much is it?"], quiet_ms=400))
    assert not d.ready and d.wait_ms == 800 and d.reason == "waiting_complete"


async def test_a_photo_alone_waits_longer_for_its_caption():
    p = HeuristicEOT()
    assert not (await p.decide(inp([""], ["image"], quiet_ms=3500))).ready
    assert (await p.decide(inp([""], ["image"], quiet_ms=4000))).ready
    assert (await p.decide(inp([""], ["audio"], quiet_ms=1200))).ready


async def test_a_slow_typist_gets_their_own_longer_wait_for_an_unfinished_message_and_not_for_a_finished_one():
    p = HeuristicEOT()
    slow, quick = (9000, 8500, 10000, 9200), (900, 1100, 800, 1000)
    unfinished = ["can you please tell me"]                           # no mark, short: the rules cannot know, so the customer's own rhythm decides
    assert (await p.decide(inp(unfinished, quiet_ms=2700, history=quick))).ready
    assert not (await p.decide(inp(unfinished, quiet_ms=2700, history=slow))).ready
    assert (await p.decide(inp(unfinished, quiet_ms=3500, history=slow))).ready
    assert (await p.decide(inp(["price of the blue kurta?"], quiet_ms=3800, history=slow))).ready


async def test_someone_firing_off_short_messages_is_given_a_little_more_time():
    p = HeuristicEOT()
    burst = dict(times_ms=[2000, 1300, 700, 0])
    assert not (await p.decide(inp(["hello", "sir", "price", "kitna"], quiet_ms=1500, **burst))).ready
    assert (await p.decide(inp(["hello", "sir", "price", "kitna"], quiet_ms=2700, **burst))).ready
    assert not (await p.decide(inp(["hello", "sir", "price?"], quiet_ms=1200, times_ms=[1500, 700, 0]))).ready      # a question mark ends it, but a rapid typist still gets 1.5 s
    assert (await p.decide(inp(["hello", "sir", "price?"], quiet_ms=1500, times_ms=[1500, 700, 0]))).ready


async def test_the_maximum_wait_always_ends_the_wait():
    d = await HeuristicEOT().decide(inp(["can you please tell me"], quiet_ms=500, waited_ms=12000))
    assert d.ready and d.reason == "max_wait"


def test_the_corpus_is_what_it_says_it_is():
    assert len(CORPUS) >= 50 and len({b.name for b in CORPUS}) == len(CORPUS)
    assert all(isinstance(b, Burst) and b.messages and b.messages[0][0] == 0 for b in CORPUS)
