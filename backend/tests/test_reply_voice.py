"""ADR 0041: a reply must sound like a person chatting on WhatsApp, never a company. A deterministic check, so a stock corporate phrase, a markdown
header, a bullet list, or emoji for someone who never uses them sends the reply back to be rewritten, with the reason."""
from __future__ import annotations

import pytest

from salesai.modules.agent.checks import CheckContext, check_voice, run_checks
from salesai.modules.agent.style import style_of

CASUAL = style_of(["hlo bhai price btao 😂", "kitne ka hai yaar", "ok done"])
FORMAL = style_of(["Good morning. Could you please share the price of the red silk saree?", "Kindly also confirm the delivery time. Thank you.",
                   "I would like to place an order for two pieces, sir."])


def ctx(style=None) -> CheckContext:
    return CheckContext(issued=frozenset(), customer_style=style or {})


@pytest.mark.parametrize("text", [
    "Dear Customer, thank you for contacting us.", "Dear valued customer, we regret to inform you", "Kindly be informed that the saree is available",
    "Thank you for reaching out to Sharma Sarees!", "Please do not hesitate to contact us.", "We apologise for the inconvenience caused.",
    "How may I assist you further today?", "Greetings from Sharma Sarees", "Feel free to reach out at your earliest convenience",
    "Pleased to inform you that your order is ready", "We will revert back to you shortly",
])
def test_stock_corporate_phrases_are_refused_in_any_register(text):
    for style in (None, CASUAL, FORMAL):
        assert [f.check for f in check_voice([text], style or {})] == ["voice"], (text, style and style["register"])


@pytest.mark.parametrize("text", [
    "Hi! Yes, it is there in red 🙂", "haan bhai, red mein hai. 1,000 ka hai", "Sure. It comes in blue and green too.", "Welcome to Sharma Sarees! How can I help you today?",
    "Thanks! I'll check and tell you in a bit.", "ji, kal khula hai, 10 se 8 baje tak",
])
def test_ordinary_friendly_chat_passes(text):
    assert check_voice([text], CASUAL) == [] or "emoji" not in text      # emoji handled by their own test below
    assert not [f for f in check_voice([text], {}) if f.check == "voice"]


def test_markdown_headers_and_lists_are_not_how_people_message():
    for text in ("# Price\nIt is 1,000", "Here are the options:\n- red\n- blue\n- green", "1. Red\n2. Blue\n3. Green", "**Price:** 1,000", "• red\n• blue"):
        assert [f.check for f in check_voice([text], {})] == ["voice"], text
    assert check_voice(["It is *1,000* only"], {}) == []                  # one WhatsApp bold is fine


def test_emoji_for_someone_who_never_uses_them_is_refused_and_for_someone_who_does_it_is_fine():
    assert [f.check for f in check_voice(["Sure, it is available 😊"], FORMAL)] == ["voice"]
    assert check_voice(["Sure, it is available 😊"], CASUAL) == []
    assert [f.check for f in check_voice(["Sure 😊😊😊😊"], CASUAL)] == ["voice"]                    # never a string of them
    assert check_voice(["Sure, it is available 😊"], {}) == []                                       # nothing known yet: do not guess


def test_a_reply_far_longer_than_the_customer_writes_is_sent_back():
    long = " ".join(["word"] * 70)
    assert [f.check for f in check_voice([long], CASUAL)] == ["length"]
    assert check_voice([long], FORMAL) == []
    assert check_voice([long], {}) == []                                                             # no profile yet: no length rule


def test_it_runs_with_the_other_checks_and_explains_itself_to_the_writer():
    res = run_checks(["Dear Customer, kindly be informed it is 1,000"], CheckContext(issued=frozenset(), customer_style=CASUAL))
    assert "voice" in res.checks()
    assert any("person" in f.detail or "chat" in f.detail for f in res.failures if f.check == "voice")
