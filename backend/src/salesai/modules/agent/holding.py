"""The one message a customer gets when the AI cannot answer (the model is down, or a reply failed its checks) and the chat is handed to the owner.

It is written in advance, in code, because the model is exactly what is not available at that moment. It is not generic: it says who the customer is
being connected to, by name, with "sir" or "madam" as is polite in India, or as "the owner" when the name is not known, and it is in the language the
customer writes, chosen from what they last wrote and the script of their message (never from a word list).

Review note: the texts below are short and plain on purpose. They should be read once by a native speaker of each language before launch.

Wording is gender-neutral on the assistant's side (passive or "we"): the assistant has no gender, and the person named gets the respectful plural.
"""
from __future__ import annotations

from collections import Counter

# language -> (message with {who}, the word for "the owner" when no name is known, sir, madam)
TEXTS: dict[str, tuple[str, str, str, str]] = {
    "en": ("Please hold on, I'm connecting you to {who}. They will reply to you shortly.", "the owner", "sir", "madam"),
    "hinglish": ("Ek minute rukiye, aapki baat {who} se karwa rahe hain. Woh jaldi hi aapko reply karenge.", "owner", "sir", "madam"),
    "hi": ("कृपया थोड़ा रुकिए, आपकी बात {who} से करवाई जा रही है। वे जल्द ही आपको जवाब देंगे।", "मालिक", "सर", "मैडम"),
    "mr": ("कृपया थोडं थांबा, तुमचं बोलणं {who} यांच्याशी करून देत आहोत. ते लवकरच तुम्हाला उत्तर देतील.", "मालक", "सर", "मॅडम"),
    "ta": ("தயவுசெய்து சற்று காத்திருங்கள், உங்களை {who} அவர்களுடன் இணைக்கிறோம். அவர்கள் விரைவில் உங்களுக்கு பதிலளிப்பார்கள்.", "உரிமையாளர்", "சார்", "மேடம்"),
    "te": ("దయచేసి కొంచెం ఆగండి, మిమ్మల్ని {who} గారితో కలుపుతున్నాము. వారు త్వరలో మీకు సమాధానం ఇస్తారు.", "యజమాని", "సర్", "మేడమ్"),
    "bn": ("অনুগ্রহ করে একটু অপেক্ষা করুন, আপনাকে {who}-এর সঙ্গে যুক্ত করা হচ্ছে। তিনি শীঘ্রই আপনাকে উত্তর দেবেন।", "মালিক", "স্যার", "ম্যাডাম"),
    "gu": ("કૃપા કરીને થોડી રાહ જુઓ, તમને {who} સાથે જોડવામાં આવી રહ્યા છે. તેઓ જલ્દી જ તમને જવાબ આપશે.", "માલિક", "સર", "મેડમ"),
    "kn": ("ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಕಾಯಿರಿ, ನಿಮ್ಮನ್ನು {who} ಅವರೊಂದಿಗೆ ಸಂಪರ್ಕಿಸಲಾಗುತ್ತಿದೆ. ಅವರು ಶೀಘ್ರದಲ್ಲೇ ನಿಮಗೆ ಉತ್ತರಿಸುತ್ತಾರೆ.", "ಮಾಲೀಕರು", "ಸರ್", "ಮೇಡಂ"),
    "ml": ("ദയവായി അൽപ്പം കാത്തിരിക്കൂ, നിങ്ങളെ {who} യുമായി ബന്ധിപ്പിക്കുന്നു. അവർ ഉടൻ നിങ്ങൾക്ക് മറുപടി നൽകും.", "ഉടമ", "സർ", "മാഡം"),
    "pa": ("ਕਿਰਪਾ ਕਰਕੇ ਥੋੜ੍ਹਾ ਰੁਕੋ, ਤੁਹਾਨੂੰ {who} ਨਾਲ ਜੋੜਿਆ ਜਾ ਰਿਹਾ ਹੈ। ਉਹ ਜਲਦੀ ਹੀ ਤੁਹਾਨੂੰ ਜਵਾਬ ਦੇਣਗੇ।", "ਮਾਲਕ", "ਸਰ", "ਮੈਡਮ"),
    "ur": ("براہِ کرم ذرا انتظار کریں، آپ کو {who} سے ملایا جا رہا ہے۔ وہ جلد آپ کو جواب دیں گے۔", "مالک", "سر", "میڈم"),
}
# one script usually means one language here; Devanagari is shared by Hindi and Marathi, so a language the planner named earlier decides between them
SCRIPT_LANGUAGE = {"devanagari": "hi", "tamil": "ta", "telugu": "te", "bengali": "bn", "gujarati": "gu", "kannada": "kn", "malayalam": "ml",
                   "gurmukhi": "pa", "arabic": "ur"}
LANGUAGE_NAMES = {"english": "en", "hindi": "hi", "marathi": "mr", "tamil": "ta", "telugu": "te", "bengali": "bn", "bangla": "bn", "gujarati": "gu",
                  "kannada": "kn", "malayalam": "ml", "punjabi": "pa", "urdu": "ur"}
TITLES = ("sir", "madam")


def _key(language: str | None) -> str | None:
    if not language:
        return None
    lang = language.strip().lower()
    lang = LANGUAGE_NAMES.get(lang, lang)
    if lang in TEXTS:
        return lang
    base = lang.replace("_", "-").split("-")[0]
    return base if base in TEXTS else None


def language_for(customer_texts: list[str], last_language: str | None = None, script_hint: str | None = None) -> str:
    """The language of the holding message. The language the planner last named for this chat wins when we have a text for it; otherwise the script of
    what the customer just wrote decides; otherwise English."""
    from salesai.modules.agent.checks import script_of
    named = _key(last_language)
    if named and named != "en":
        return named
    letters = [c for t in customer_texts for c in t if c.isalpha()]
    script = max(Counter(script_of(c) for c in letters).items(), key=lambda kv: kv[1])[0] if letters else (script_hint or "latin")
    return SCRIPT_LANGUAGE.get(script, named or "en")


def owner_reference(lang: str, owner_name: str | None, owner_title: str | None) -> str:
    """"Rajesh sir", "Priya madam", "Rajesh" (no title known), or "the owner" when no name is known."""
    _, owner_word, sir, madam = TEXTS[lang]
    name = " ".join((owner_name or "").split())
    if not name:
        return owner_word
    title = (owner_title or "").strip().lower()
    if title in TITLES:
        return f"{name} {sir if title == 'sir' else madam}"
    return name


def holding_message(customer_texts: list[str], *, owner_name: str | None, owner_title: str | None = None,
                    last_language: str | None = None, script_hint: str | None = None) -> str:
    lang = language_for(customer_texts, last_language, script_hint)
    return TEXTS[lang][0].format(who=owner_reference(lang, owner_name, owner_title))
