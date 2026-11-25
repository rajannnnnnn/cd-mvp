"""A hand-written corpus of customer bursts for judging end-of-turn detection (ADR 0036). Each burst is what one customer sent in one go:
(milliseconds since their first message, text, kind). The label is built in: the AI should answer after the LAST message of the burst, not
before it, and not long after. English, Hinglish and other Indian languages, as WhatsApp customers write them.

Honest limits: written by the engineer, small, and clean. It is a regression gate and a way to compare predictors, not a measurement of
production accuracy; real pilot chats replace it as soon as they exist."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Burst:
    name: str
    messages: tuple[tuple[int, str, str], ...]              # (t_ms, text, kind)
    ai_asked: bool = False                                  # the AI's last message ended with a question: a short reply is an answer
    history_gaps_ms: tuple[int, ...] = field(default_factory=tuple)   # this customer's earlier gaps between messages (their rhythm)


def one(name: str, text: str, **kw) -> Burst:
    return Burst(name, ((0, text, "text"),), **kw)


def seq(name: str, *parts: tuple[int, str], kind: str = "text", **kw) -> Burst:
    return Burst(name, tuple((t, x, kind) for t, x in parts), **kw)


CORPUS: tuple[Burst, ...] = (
    # --- one complete message (English): answer quickly
    one("en-price", "How much is the red silk saree?"),
    one("en-stock", "Do you have this in size M"),
    one("en-hours", "What time do you open tomorrow?"),
    one("en-delivery", "Can you deliver to Pune by Friday"),
    one("en-long-no-punct", "I saw your post on Instagram and want to know if the blue lehenga is still available"),
    one("en-statement", "I will come to the shop at 6 today."),
    one("en-short-question", "Price?"),
    one("en-thanks", "Thanks a lot!"),
    # --- one complete message (Hinglish), usually without punctuation
    one("hi-price", "ye saree ka price kya hai"),
    one("hi-colour", "mujhe red colour chahiye"),
    one("hi-delivery", "delivery kitne din me hogi"),
    one("hi-open", "kal shop khula hai kya"),
    one("hi-show", "ek kurta dikhao"),
    one("hi-short-price", "price batao"),
    one("hi-available", "ye available hai"),
    one("hi-discount", "thoda discount milega kya"),
    # --- a greeting, then the real question
    seq("greeting-then-question-fast", (0, "hi"), (1800, "do you have silk sarees")),
    seq("greeting-then-question-slow", (0, "hello"), (3500, "do you have silk sarees under 5000?")),
    seq("greeting-hinglish", (0, "namaste"), (2600, "mujhe ek lehenga chahiye shaadi ke liye")),
    seq("greeting-hey", (0, "hey"), (2200, "is the black kurta in stock")),
    seq("greeting-with-name", (0, "hi"), (1500, "this is Priya"), (2400, "I want to order a saree")),
    seq("greeting-hii", (0, "hii"), (3000, "price of the green dupatta?")),
    # --- one thought broken into pieces
    seq("fragments-wedding", (0, "I want to buy"), (1900, "a red saree"), (2400, "for my sister's wedding")),
    seq("fragments-and", (0, "I need two kurtas and"), (2100, "one dupatta")),
    seq("fragments-comma", (0, "blue one,"), (1700, "medium size,"), (2300, "do you have it?")),
    seq("fragments-hinglish", (0, "mujhe wo wali"), (2000, "saree chahiye jo kal"), (2500, "instagram pe dikhayi thi")),
    seq("fragments-with-dots", (0, "so I was thinking..."), (3000, "can I get a discount on two sarees")),
    seq("fragments-article", (0, "do you have the"), (2200, "cotton kurta in green")),
    seq("fragments-preposition", (0, "can you deliver to"), (2400, "Hyderabad?")),
    seq("fragments-conjunction", (0, "I like the first one but"), (2600, "the price is high for me")),
    # --- a short answer to a question the AI just asked: it is complete
    one("answer-colour", "red", ai_asked=True),
    one("answer-yes", "yes", ai_asked=True),
    one("answer-number", "2", ai_asked=True),
    one("answer-city", "Pune", ai_asked=True),
    one("answer-time", "tomorrow evening", ai_asked=True),
    one("answer-hinglish", "haan chalega", ai_asked=True),
    one("answer-size", "M", ai_asked=True),
    # --- photos and voice
    Burst("photo-then-caption", ((0, "", "image"), (2300, "is this available?", "text"))),
    Burst("photo-then-question-slow", ((0, "", "image"), (3600, "do you have this in blue", "text"))),
    Burst("photo-with-two-lines", ((0, "", "image"), (1800, "this one", "text"), (2600, "what is the price", "text"))),
    Burst("photo-alone", ((0, "", "image"),)),
    Burst("voice-note", ((0, "", "audio"),)),
    Burst("voice-then-text", ((0, "", "audio"), (2800, "sorry, in short: need it by Friday", "text"))),
    # --- rapid fire
    seq("rapid-hello-sir", (0, "hello"), (700, "sir"), (1100, "price?"), (1900, "of the blue one")),
    seq("rapid-three", (0, "saree"), (800, "red"), (1500, "price")),
    seq("rapid-hinglish", (0, "bhaiya"), (900, "ye"), (1600, "kitne ka hai")),
    seq("rapid-question-marks", (0, "do you deliver?"), (1000, "to Mumbai?"), (1700, "how many days?")),
    # --- a slow typist (their own history says gaps are long)
    seq("slow-typist-1", (0, "I wanted to ask about"), (7800, "the lehenga price"), history_gaps_ms=(8000, 9500, 7000, 8800)),
    seq("slow-typist-2", (0, "can you please tell me"), (9000, "if the red saree is available?"), history_gaps_ms=(9000, 8500, 10000, 9200)),
    seq("slow-typist-3", (0, "hello"), (8200, "do you have kurtas"), history_gaps_ms=(8000, 7500, 9000)),
    seq("fast-typist-complete", (0, "price of the blue kurta?"), history_gaps_ms=(900, 1100, 800, 1000)),
    # --- sentence-final words without punctuation, two messages
    seq("hinglish-two-part", (0, "mujhe ek saree chahiye"), (2000, "red colour me")),
    seq("english-two-part-no-punct", (0, "I want a saree"), (1900, "in red colour for my mother")),
    # --- other Indian and foreign languages: nothing in the rules knows their words, only how a message ends and how long it is
    one("ta-price", "இந்த புடவையின் விலை என்ன?"),
    one("bn-price", "এই শাড়ির দাম কত?"),
    one("ar-price", "كم سعر هذا الفستان؟"),
    one("zh-long-no-punct", "我想买一件红色的丝绸纱丽给我妹妹的婚礼"),
    one("mr-no-punct", "ही साडी किती रुपयांना आहे"),
    one("te-answer", "ఎరుపు", ai_asked=True),
    seq("ta-fragments", (0, "எனக்கு ஒரு சிவப்பு"), (2000, "புடவை வேண்டும்")),
    seq("bn-greeting-then-question", (0, "নমস্কার"), (2400, "আপনার কাছে সিল্কের শাড়ি আছে?")),
)
