"""ADR 0041: the AI talks the way the customer talks: their language and script mix, how casual or professional they are, how long they write,
whether they use emoji. Worked out by rules from the customer's own messages in this chat (no model call, no cost), per customer only, and
handed to the writer. Never a corporate voice: that is checked, and a reply that sounds like a support desk is rewritten."""
from __future__ import annotations

import pytest

from salesai.modules.agent.style import reply_hint, style_of

CASUAL = ["hlo bhai price btao 😂", "kitne ka hai yaar", "ok done", "lol thik hai"]
FORMAL = ["Good morning. Could you please share the price of the red silk saree?", "Kindly also confirm the delivery time. Thank you.",
          "I would like to place an order for two pieces, sir."]
NEUTRAL = ["Do you have this in blue", "what is the price", "can you deliver to Pune"]
HINGLISH = ["mujhe red saree chahiye", "ye kitne ka hai", "delivery kab tak hogi"]
HINDI = ["मुझे लाल साड़ी चाहिए", "इसकी कीमत क्या है", "कल दुकान खुली है क्या"]


def test_a_very_casual_customer():
    s = style_of(CASUAL)
    assert s["register"] in ("very_casual", "casual") and s["register_score"] < 0.4
    assert s["uses_emoji"] is True and s["lowercase_only"] is True and s["length"] == "terse" and s["punctuation"] == "none"
    assert "bhai" in s["address_terms"]


def test_a_professional_customer():
    s = style_of(FORMAL)
    assert s["register"] in ("professional", "formal") and s["register_score"] > 0.6
    assert s["uses_emoji"] is False and s["lowercase_only"] is False and s["punctuation"] == "proper" and s["length"] in ("medium", "long")
    assert "sir" in s["address_terms"]


def test_a_plain_customer_is_neutral():
    s = style_of(NEUTRAL)
    assert s["register"] == "neutral" and s["length"] in ("short", "terse") and s["uses_emoji"] is False


def test_language_and_script():
    h = style_of(HINGLISH)
    assert h["language"]["hinglish"] > 0.6 and h["script"] == "latin" and h["dominant_language"] == "hinglish"
    d = style_of(HINDI)
    assert d["script"] == "devanagari" and d["dominant_language"] == "hi"
    e = style_of(NEUTRAL)
    assert e["dominant_language"] == "en" and e["language"]["en"] > 0.8
    mixed = style_of(HINGLISH + NEUTRAL)
    assert mixed["language"]["hinglish"] > 0.2 and mixed["language"]["en"] > 0.2          # a customer who switches is described as one


def test_nothing_known_yet_is_neutral_and_says_so():
    s = style_of([])
    assert s["messages"] == 0 and s["register"] == "neutral" and s["uses_emoji"] is False and s["dominant_language"] == "en"


def test_only_the_recent_messages_count_so_a_customer_who_changes_tone_is_followed():
    old_formal = FORMAL * 3
    s = style_of(old_formal + CASUAL * 3)                  # formal at first, casual lately
    assert s["register"] in ("very_casual", "casual")
    assert s["messages"] <= 12


def test_it_is_deterministic():
    assert style_of(CASUAL + FORMAL) == style_of(CASUAL + FORMAL)


@pytest.mark.parametrize("texts,max_words", [(CASUAL, 20), (NEUTRAL, 35), (FORMAL, 80)])
def test_the_reply_is_asked_to_be_about_as_long_as_the_customer_writes(texts, max_words):
    h = reply_hint(style_of(texts))
    assert h["max_words"] <= max_words and h["messages"] == 1
    assert reply_hint(style_of(CASUAL))["max_words"] < reply_hint(style_of(FORMAL))["max_words"]


def test_emoji_are_only_for_customers_who_use_them():
    assert reply_hint(style_of(CASUAL))["emoji"] == "one_is_fine"
    assert reply_hint(style_of(FORMAL))["emoji"] == "none"
    assert reply_hint(style_of([]))["emoji"] == "none"
