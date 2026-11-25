"""Founder, 2026-11-24: the customer is answered in the language they write, whichever it is, and no language is special-cased in code. The model reads
and writes the languages; the code only checks what is the same everywhere: the writing system, the numbers, the claims. ADR 0051."""
from __future__ import annotations

import pytest

from salesai.modules.agent import checks as chk
from salesai.modules.agent import prompts
from salesai.modules.agent.models import CheckOutput, PlannerOutput
from salesai.modules.agent.style import style_of

TAMIL = "இந்த புடவையின் விலை 4500 ரூபாய்"
BENGALI = "এই শাড়িটির দাম 4500 টাকা"
ARABIC = "سعر هذا الفستان 4500 روبية"
HINDI = "इस साड़ी की कीमत 4500 रुपये है"
ENGLISH = "The price of this saree is 4500 rupees"


def planner(**kw) -> PlannerOutput:
    return PlannerOutput.model_validate({"intent": "greeting", **kw})


# ---------------------------------------------------------------- the planner may name any language and any script

@pytest.mark.parametrize("lang,script", [("ta", "tamil"), ("Tamil", "Tamil"), ("bn", "bengali"), ("mr", "devanagari"), ("hinglish", "latin"),
                                         ("ur", "arabic"), ("zh-CN", "han"), ("pa-IN", "gurmukhi"), ("some language we have not heard of", "unknown script")])
def test_any_language_and_script_is_accepted(lang, script):
    out = planner(language=lang, script=script)
    assert out.language == lang.lower() and out.script == script.lower()


def test_a_garbled_label_never_fails_the_turn():
    assert planner(language=None, script=7).language == "en"
    assert planner(language="", script="  ").script == "latin"
    out = planner(language="ta<script>alert(1)</script>" + "x" * 100)
    assert "<" not in out.language and len(out.language) <= 40


def test_the_schema_tells_the_model_what_to_put_there():
    props = PlannerOutput.model_json_schema()["properties"]
    assert "ta" in props["language"]["description"] and "tamil" in props["script"]["description"]


# ---------------------------------------------------------------- the reply is in the customer's script, whatever it is

def ctx(script: str, language: str = "ta", **kw) -> chk.CheckContext:
    return chk.CheckContext(issued=frozenset({chk.Decimal("4500")}), customer_script=script, language=language, **kw)


@pytest.mark.parametrize("script,good,bad", [("tamil", TAMIL, ENGLISH), ("bengali", BENGALI, TAMIL), ("arabic", ARABIC, ENGLISH),
                                             ("devanagari", HINDI, TAMIL), ("latin", ENGLISH, TAMIL)])
def test_the_reply_must_be_in_the_customers_script(script, good, bad):
    assert not [f for f in chk.run_checks([good], ctx(script)).failures if f.check == "script"]
    assert [f for f in chk.run_checks([bad], ctx(script)).failures if f.check == "script"]


def test_a_shop_or_product_name_in_latin_inside_a_sentence_in_another_script_is_fine():
    text = "Banarasi Silk Saree இந்த புடவை 4500 ரூபாய்"
    assert not [f for f in chk.run_checks([text], ctx("tamil")).failures if f.check == "script"]


def test_a_script_the_rules_do_not_list_is_not_judged():
    assert chk.check_script(TAMIL, "klingon") is None
    assert chk.check_script(TAMIL, "") is None


def test_the_script_of_a_letter_comes_from_unicode_not_from_a_list_of_languages():
    assert chk.script_of("த") == "tamil" and chk.script_of("ব") == "bengali" and chk.script_of("ع") == "arabic" and chk.script_of("中") == "han"


# ---------------------------------------------------------------- numbers are still checked in every language

def test_a_number_the_engine_did_not_issue_is_rejected_in_any_language():
    for text in (TAMIL.replace("4500", "3900"), BENGALI.replace("4500", "3900"), ARABIC.replace("4500", "3900")):
        assert [f for f in chk.run_checks([text], ctx("tamil")).failures if f.check == "price"], text


# ---------------------------------------------------------------- the word patterns only judge the languages they know

def test_which_languages_the_word_patterns_can_judge():
    assert all(chk.lexical(x) for x in ("en", "hi", "hinglish", "English", "en-IN", "hi_IN"))
    assert not any(chk.lexical(x) for x in ("ta", "bn", "mr", "te", "ur", "zh"))


def test_a_correct_tamil_reply_to_are_you_a_bot_is_not_rejected_by_english_word_patterns():
    reply = "நான் இந்தக் கடையின் உதவியாளர். உரிமையாளரிடம் இணைக்கிறேன்."
    r = chk.run_checks([reply], ctx("tamil", sincere_identity_question=True))
    assert not [f for f in r.failures if f.check == "identity"]
    # the same reply in a language the patterns know, without the disclosure words, is still rejected
    r = chk.run_checks(["Sure, how can I help with the saree?"], ctx("latin", "en", sincere_identity_question=True))
    assert [f for f in r.failures if f.check == "identity"]


def test_the_checker_prompt_asks_about_identity_and_judges_any_language():
    p = prompts.load("checker")
    assert "identity_ok" in p.text and "must_disclose" in p.text and "any language" in p.text
    assert CheckOutput().identity_ok is True


# ---------------------------------------------------------------- the prompts say "any language", not "English, Hindi or Hinglish"

def test_the_active_prompts_do_not_limit_the_languages():
    for pid in ("planner", "writer"):
        text = prompts.load(pid).text
        assert "(English, Hindi, or Hinglish)" not in text and "English, Hindi, or Hinglish = Hindi in Latin script" not in text, pid
    w = prompts.load("writer").text
    assert "Tamil" in w and "Do not translate them into English or Hindi" in w
    assert "any" in prompts.load("planner").text and "tamil" in prompts.load("planner").text


# ---------------------------------------------------------------- the style profile reads scripts, not languages

def test_style_describes_the_script_in_any_language():
    t = style_of(["இந்த புடவை எவ்வளவு", "நீல நிறம் இருக்கிறதா", "டெலிவரி எப்போது"])
    assert t["script"] == "tamil" and t["scripts"] == {"tamil": 1.0}
    assert style_of(["এই শাড়ির দাম কত", "ডেলিভারি কবে"])["script"] == "bengali"
    assert style_of(["price kitna hai", "delivery kab hogi"])["script"] == "latin"
    mixed = style_of(["இந்த புடவை எவ்வளவு", "price please", "ok thanks", "நீல நிறம்"])
    assert set(mixed["scripts"]) == {"tamil", "latin"}
    assert "language" not in t and "dominant_language" not in t          # no language is guessed from word lists


def test_length_is_counted_in_any_script():
    assert style_of(["இந்த புடவை எவ்வளவு விலை என்று சொல்லுங்கள் தயவு செய்து"])["median_words"] >= 7
    assert style_of(["这件红色丝绸纱丽多少钱请告诉我谢谢"])["median_words"] >= 5        # no spaces: counted by characters, not as one word
