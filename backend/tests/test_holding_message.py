"""When the AI cannot answer (the model is down, or a reply keeps failing its checks) the customer gets ONE message that names who they are being
connected to ("Rajesh sir", "Priya madam", or "the owner" when no name is known), in the language they write, and then the chat is handed to the owner
and the AI stays out. Founder, 2026-11-24. The message is not generic and it is not English by default."""
from __future__ import annotations

import pytest

from salesai.modules.agent import holding as H
from salesai.modules.agent.llm.local import LocalRulesProvider
from tests.test_conversation_flow import DownProvider, shop_with_catalog
from tests.world import Recorder, install_llm, settle

TAMIL = "இந்த புடவை எவ்வளவு"
BENGALI = "এই শাড়ির দাম কত"
HINDI = "इस साड़ी की कीमत क्या है"


# ---------------------------------------------------------------- who the customer is connected to

def test_the_person_is_named_with_sir_or_madam():
    assert H.owner_reference("en", "Rajesh", "sir") == "Rajesh sir"
    assert H.owner_reference("en", "Priya", "madam") == "Priya madam"
    assert H.owner_reference("hi", "Rajesh", "sir") == "Rajesh सर"
    assert H.owner_reference("ta", "Priya", "madam") == "Priya மேடம்"


def test_with_a_name_but_no_title_the_name_alone_is_used_and_a_title_is_never_guessed_from_it():
    assert H.owner_reference("en", "Sam", None) == "Sam"
    assert H.owner_reference("en", "Sam", "") == "Sam"
    assert H.owner_reference("en", "Sam", "doctor") == "Sam"                 # only sir or madam: nothing else is ever invented


def test_without_a_name_the_owner_is_called_the_owner_in_their_language():
    assert H.owner_reference("en", None, "sir") == "the owner"
    assert H.owner_reference("en", "  ", None) == "the owner"
    assert H.owner_reference("ta", "", None) == "உரிமையாளர்"
    assert H.owner_reference("hi", None, None) == "मालिक"


def test_every_language_has_one_place_for_the_name_and_all_its_words():
    for lang, (text, owner_word, sir, madam) in H.TEXTS.items():
        assert text.count("{who}") == 1, lang
        assert owner_word and sir and madam, lang
        out = H.holding_message(["x"], owner_name="Asha", owner_title="madam", last_language=lang)
        assert "{" not in out and "}" not in out and madam in out and "Asha" in out, lang


# ---------------------------------------------------------------- the language comes from the customer, never from a word list

def test_the_language_follows_what_the_customer_wrote():
    assert H.language_for([TAMIL]) == "ta"
    assert H.language_for([BENGALI]) == "bn"
    assert H.language_for([HINDI]) == "hi"
    assert H.language_for(["price please"]) == "en"
    assert H.language_for([]) == "en"


def test_the_language_the_planner_named_for_this_chat_decides_where_the_script_cannot():
    assert H.language_for(["price kitna hai"], last_language="hinglish") == "hinglish"      # Hindi typed in Latin letters
    assert H.language_for([HINDI], last_language="mr") == "mr"                              # Devanagari is shared by Hindi and Marathi
    assert H.language_for([HINDI], last_language=None) == "hi"
    assert H.language_for(["hello"], last_language="Tamil") == "ta"                         # a plain name works as well as a code
    assert H.language_for(["hello"], last_language="xx") == "en"                            # a language we have no text for is answered in English
    assert H.language_for([BENGALI], last_language="en") == "bn"                            # the customer changed language: the script wins over English


def test_the_message_says_please_hold_on_and_who():
    en = H.holding_message(["hi"], owner_name="Rajesh", owner_title="sir")
    assert "hold on" in en.lower() and "Rajesh sir" in en and "connecting you" in en
    assert "the owner" in H.holding_message(["hi"], owner_name=None)


# ---------------------------------------------------------------- end to end: one message, then the AI stays out

async def test_when_the_model_is_down_one_named_message_is_sent_in_the_customers_language_then_the_chat_is_handed_over(world):
    shop, _ = await shop_with_catalog(world)
    await world.q(shop, "UPDATE businesses SET sales_settings = COALESCE(sales_settings, '{}'::jsonb) || '{\"owner_title\": \"sir\"}'::jsonb WHERE id=%s RETURNING id",
                  shop.business_id)
    await world.q(shop, "UPDATE business_users SET name='Rajesh' WHERE role='owner' RETURNING id")
    old = install_llm(world, DownProvider())
    phone = "+919700000090"
    try:
        await world.customer_says(shop, phone, TAMIL)
        texts = await settle(world, shop, phone)
        assert len(texts) == 1 and "Rajesh" in texts[0] and "சார்" in texts[0], texts          # Tamil, with the owner's name and sir
        [h] = await world.q(shop, "SELECT reason, status FROM handoffs")
        assert h["reason"] == "system_failure" and h["status"] == "open"
        # the customer writes again while the chat is with the owner: recorded, never answered
        await world.customer_says(shop, phone, "ஹலோ, யாராவது இருக்கிறீர்களா?")
        await world.drain_for(2.0)
        out = await world.q(shop, "SELECT body FROM messages WHERE direction='out' AND sender='ai' AND status <> 'cancelled'")
        assert len(out) == 1                                                                  # still the one message
        [cv] = await world.q(shop, "SELECT ai_paused_until > now() AS paused, ai_paused_reason FROM conversations")
        assert cv["paused"] and cv["ai_paused_reason"] == "handoff"
    finally:
        world.rt.agent.llm = old


async def test_without_an_owner_title_the_name_alone_is_used_and_in_english_by_default(world):
    shop, _ = await shop_with_catalog(world)
    await world.q(shop, "UPDATE business_users SET name='Rajesh' WHERE role='owner' RETURNING id")
    old = install_llm(world, DownProvider())
    try:
        await world.customer_says(shop, "+919700000091", "hello")
        [text] = await settle(world, shop, "+919700000091")
    finally:
        world.rt.agent.llm = old
    assert "connecting you to Rajesh." in text and "sir" not in text and "madam" not in text


@pytest.mark.parametrize("title", ["sir", "madam"])
def test_the_title_is_set_by_the_owner_through_the_sales_settings(title):
    from salesai.api.routes.business import SalesSettingsIn
    assert SalesSettingsIn(owner_title=title).owner_title == title
    with pytest.raises(ValueError):
        SalesSettingsIn(owner_title="boss")


async def test_the_language_of_the_chat_is_remembered_so_the_message_fits_when_the_model_goes_down_later(world):
    shop, _ = await shop_with_catalog(world)
    rec = Recorder(LocalRulesProvider())
    old = install_llm(world, rec)
    phone = "+919700000092"
    try:
        await world.customer_says(shop, phone, "banarasi saree kitne ka hai yaar")           # Hindi in Latin letters: the stand-in planner names it hinglish
        await settle(world, shop, phone)
        [cv] = await world.q(shop, "SELECT qualification FROM conversations")
        assert cv["qualification"].get("last_language") == "hinglish"
    finally:
        world.rt.agent.llm = old
