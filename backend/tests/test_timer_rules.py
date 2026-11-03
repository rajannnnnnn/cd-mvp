"""ADR 0038: when the agent's clock should wake it, as plain rules over the state of one conversation. No database, no model: given what
has happened in a chat, which timers should exist and when are they due."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from salesai.modules.conversations.timer_rules import GRACE, ConvState, desired

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)          # 17:30 in India: a shop is open


def state(**kw) -> ConvState:
    base = ConvState(now=NOW, ai_allowed=True, paused_until=None, pause_reason=None, last_inbound_at=NOW - timedelta(minutes=10),
                     last_outbound_at=NOW - timedelta(minutes=5), last_speaker="ai", waiting_unanswered=False, waiting_released=False,
                     queued_ai=False, lead_stage="interested", selling_stopped=False, follow_back_after=None, rhythm_minutes=None,
                     follow_back_minutes=240, hours=None, tz="Asia/Kolkata", hours_behavior="reply_normally")
    return replace(base, **kw)


def next_open(profile, tz, now):                         # a shop open 10:00-20:00 IST every day: next opening after `now`
    from salesai.modules.delivery.hours import next_open as real
    return real(profile, tz, now)


HOURS = {"hours": {d: "10:00-20:00" for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}


def want(**kw):
    return desired(state(**kw), next_open)


# ---------------------------------------------------------------- follow back: the customer went quiet after the AI replied

def test_after_an_ai_reply_to_an_interested_customer_a_follow_back_is_planned_at_the_shops_usual_gap():
    w = want()["follow_back"]
    assert w.due_at == NOW - timedelta(minutes=5) + timedelta(minutes=240)
    assert w.reason == "ai_replied" and "240" in w.why


def test_a_customer_who_usually_answers_fast_is_followed_up_sooner_but_never_inside_half_an_hour():
    assert want(rhythm_minutes=20)["follow_back"].due_at == NOW - timedelta(minutes=5) + timedelta(minutes=80)       # four times their usual wait
    assert want(rhythm_minutes=3)["follow_back"].due_at == NOW - timedelta(minutes=5) + timedelta(minutes=30)        # floor


def test_a_slow_customer_is_not_followed_up_later_than_the_shops_own_setting():
    assert want(rhythm_minutes=600)["follow_back"].due_at == NOW - timedelta(minutes=5) + timedelta(minutes=240)


def test_the_closer_to_buying_the_sooner():
    assert want(lead_stage="ready_to_buy")["follow_back"].due_at == NOW - timedelta(minutes=5) + timedelta(minutes=120)
    assert want(lead_stage="negotiating")["follow_back"].due_at == NOW - timedelta(minutes=5) + timedelta(minutes=180)
    assert want(lead_stage="ready_to_buy", follow_back_minutes=40)["follow_back"].due_at == NOW - timedelta(minutes=5) + timedelta(minutes=30)


def test_no_follow_back_for_someone_who_has_not_shown_interest_or_is_done():
    for stage in ("new", "exploring", "won", "lost"):
        assert "follow_back" not in want(lead_stage=stage)
    assert "follow_back" not in want(selling_stopped=True)


def test_no_follow_back_when_it_would_land_outside_the_free_window():
    # the customer last wrote 21 hours ago: 4 hours later is past the 22-hour limit on free-form messages
    assert "follow_back" not in want(last_inbound_at=NOW - timedelta(hours=21), last_outbound_at=NOW - timedelta(minutes=5))


def test_only_one_follow_back_per_customer_message():
    assert "follow_back" not in want(follow_back_after=NOW - timedelta(minutes=10))
    assert "follow_back" in want(follow_back_after=NOW - timedelta(hours=3))             # an older message: the customer has spoken since


def test_a_follow_back_waits_for_the_shop_to_open_when_the_owner_asked_for_that():
    night = NOW.replace(hour=20, minute=30)                                              # 02:00 IST
    base = dict(now=night, last_outbound_at=night - timedelta(minutes=5), last_inbound_at=night - timedelta(minutes=10), hours=HOURS)
    w = want(**base, hours_behavior="wait_for_open")["follow_back"]
    assert w.due_at == datetime(2026, 11, 4, 4, 30, tzinfo=UTC) + timedelta(minutes=5)       # 10:00 IST the next morning, plus five minutes
    assert want(**base, hours_behavior="reply_normally")["follow_back"].due_at == night - timedelta(minutes=5) + timedelta(minutes=240)


def test_no_follow_back_while_a_reply_is_still_waiting_to_be_sent_or_the_customer_is_the_last_to_speak():
    assert "follow_back" not in want(queued_ai=True)
    assert "follow_back" not in want(last_speaker="customer", waiting_unanswered=True)
    assert "follow_back" not in want(last_speaker="owner")


def test_nothing_at_all_when_the_ai_may_not_speak():
    assert want(ai_allowed=False) == {}


# ---------------------------------------------------------------- take over: the owner stepped in, then went quiet

def test_when_the_owner_steps_in_the_ai_checks_again_when_the_pause_ends():
    until = NOW + timedelta(minutes=90)
    w = want(paused_until=until, pause_reason="owner_reply", last_speaker="owner")
    assert set(w) == {"takeover_check"} and w["takeover_check"].due_at == until + GRACE and w["takeover_check"].reason == "owner_reply"


def test_a_hand_off_or_a_manual_pause_is_checked_the_same_way():
    until = NOW + timedelta(hours=2)
    for reason in ("handoff", "manual"):
        assert want(paused_until=until, pause_reason=reason)["takeover_check"].reason == reason


def test_when_the_pause_is_over_and_the_customer_is_still_waiting_the_ai_takes_over_now():
    w = want(paused_until=NOW - timedelta(minutes=1), pause_reason="owner_reply", last_speaker="customer", waiting_released=True)
    assert w["takeover_check"].due_at == NOW and w["takeover_check"].reason == "customer_waiting"
    assert "follow_back" not in w


def test_a_waiting_message_the_normal_path_still_owns_is_not_taken_over():
    assert want(last_speaker="customer", waiting_unanswered=True) == {}


def test_nobody_waiting_and_no_pause_means_nothing_to_do_but_the_follow_back():
    assert set(want()) == {"follow_back"}
    assert want(last_speaker="owner") == {}
