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
    assert v2["wait_p50_ms"] <= 1500 and v2["wait_p95_ms"] <= 3500 and v2["wait_mean_ms"] <= 2000, v2
    # the two it still gets wrong are known and intended: a voice note followed by a text, and a complete sentence followed by an extra detail
    assert {o.burst for o in out if o.premature} <= {"voice-then-text", "hinglish-two-part"}


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
    ("How much is the red silk saree?", "complete"), ("price?", "complete"), ("Thanks a lot!", "complete"), ("I will come at 6.", "complete"),
    ("ye saree ka price kya hai", "complete"), ("mujhe red colour chahiye", "complete"), ("price batao", "complete"), ("kal shop khula hai kya", "complete"),
    ("I saw your post on Instagram and want to know if the blue lehenga is still available", "complete"),
    ("hi", "open"), ("hello sir", "open"), ("namaste", "open"), ("so I was thinking...", "open"), ("I need two kurtas and", "open"),
    ("blue one,", "open"), ("can you deliver to", "open"), ("can you please tell me", "open"), ("do you have the", "open"), ("saree", "open"),
    ("I want to buy", "partial"), ("I want a saree", "partial"), ("mujhe wo wali", "partial"),
    ("do you have silk sarees", "unsure"), ("the price is high for me", "unsure"),
])
def test_a_single_message_is_classified(text, expect):
    assert classify(text, "text") == expect


def test_a_short_reply_to_a_question_the_ai_just_asked_is_an_answer_not_a_fragment():
    for t in ("red", "yes", "2", "Pune", "tomorrow evening", "haan chalega"):
        assert classify(t, "text", ai_asked=True) == "complete"
        assert classify(t, "text", ai_asked=False) in ("open", "unsure", "complete")           # without the question, the same words may be a fragment
    assert classify("red", "text", ai_asked=False) == "open"


def test_photos_and_voice_notes():
    assert classify("", "image") == "open"                                # a photo alone usually has its question coming
    assert classify("", "audio") == "complete"                            # a voice note is a whole thought
    assert classify("is this available?", "image") == "complete"          # a caption is judged like any text


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
    history = (9000, 8500, 10000, 9200)
    assert not (await p.decide(inp(["can you please tell me"], quiet_ms=6000, history=history))).ready
    assert (await p.decide(inp(["price of the blue kurta?"], quiet_ms=3800, history=history))).ready


async def test_someone_firing_off_short_messages_is_given_a_little_more_time():
    p = HeuristicEOT()
    burst = dict(times_ms=[2000, 1300, 700, 0])
    assert not (await p.decide(inp(["hello", "sir", "price", "kitna"], quiet_ms=1200, **burst))).ready
    assert (await p.decide(inp(["hello", "sir", "price", "kitna"], quiet_ms=1500, **burst))).ready


async def test_the_maximum_wait_always_ends_the_wait():
    d = await HeuristicEOT().decide(inp(["can you please tell me"], quiet_ms=500, waited_ms=12000))
    assert d.ready and d.reason == "max_wait"


def test_the_corpus_is_what_it_says_it_is():
    assert len(CORPUS) >= 50 and len({b.name for b in CORPUS}) == len(CORPUS)
    assert all(isinstance(b, Burst) and b.messages and b.messages[0][0] == 0 for b in CORPUS)
