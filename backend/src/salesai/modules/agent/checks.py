"""Automated reply checks — every outbound reply passes these before sending (Technical Design: LLM
orchestration). Pure functions, no I/O. A failed check leads to one regeneration, then a safe holding
message plus handoff.

  INV-2  every price/discount/offer value in a reply must be one the pricing engine issued this turn
  INV-9  no human claim, no invented urgency, stay scoped
  CR-8   never ask for OTP / card / password / PIN
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

DEV = re.compile(r"[ऀ-ॿ]")
MARK_BEFORE = re.compile(r"(?:₹|rs\.?|inr|rupees?|rupaye|rupay|रुपये|रु\.?)\s*$", re.I)
MARK_AFTER = re.compile(r"^\s*(?:/-|rs\b|rs\.|inr\b|rupees?\b|rupaye\b|रुपये|रुपए|रु\b|only\b|ka\b|ke\b|mein\b|me\b|%|percent\b|off\b|discount\b|chhoot\b|छूट)", re.I)
NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(k\b|thousand\b|hazar\b|hazaar\b|हज़ार|हजार|lakh\b|lac\b|लाख)?", re.I)
TIME_LIKE = re.compile(r"^\s*(?::\d{2}|\s?(?:am|pm)\b|\s?(?:st|nd|rd|th)\b|/\d)", re.I)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b|per cent\b|प्रतिशत)", re.I)

WORD_AMT = re.compile(
    r"\b(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|ek|do|teen|char|paanch|panch|chhe|saat|aath|nau|das|bees|pachas|saath|assi|nabbe)"
    r"(?:[\s-]+(?:one|two|three|four|five|six|seven|eight|nine|ek|do|teen|char|paanch))?\s+(?:hundred|thousand|lakh|sau|hazar|hazaar|sou))\b", re.I)

URGENCY = [
    ("low_stock", re.compile(r"\b(only \d+ (?:left|remaining|pieces? left)|last (?:piece|one|few)|few (?:pieces )?left|almost (?:gone|sold out)|running (?:low|out)|sirf \d+ (?:bache|bachi|bacha)|\d+ hi bache|aakhri (?:piece|ek)|stock (?:khatam|kam)|(?:केवल|सिर्फ़|सिर्फ) \d+ (?:बचे|बचा)|आखिरी (?:पीस|एक))\b|(?:केवल|सिर्फ़|सिर्फ) \d+ (?:बचे|बचा)|आखिरी (?:पीस|एक)", re.I)),
    ("offer_ends", re.compile(r"\b(offer (?:ends|expires|valid (?:till|until)|is valid)|valid (?:till|until)|ends (?:today|tomorrow|soon|on)|expires?|offer (?:khatam|tak valid)|tak valid hai)\b|ऑफ़र .{0,12}(?:खत्म|तक मान्य)|तक मान्य", re.I)),
    ("pressure", re.compile(r"\b(hurry|act now|don'?t miss|grab (?:it|now|yours)|limited (?:time|stock|offer|period|seats)|selling fast|before (?:it'?s|they'?re) gone|once in a lifetime|today only|last chance|jaldi (?:karo|kijiye|kare|karein)|abhi (?:le lo|lelo|kharido)|mauka (?:nahi|na)|jald hi khatam|जल्दी करें|जल्दी कीजिए|मौका न|आज ही)\b|जल्दी करें|जल्दी कीजिए|आज ही", re.I)),
]
HUMAN_CLAIM = re.compile(r"\b(i am|i'm|main|mai|im) (?:a |an )?(?:real |actual )?(?:human|person|man|woman|insaan|admin|owner himself|the owner)\b|\b(?:as a|being a) human\b|\bnot (?:a )?(?:bot|robot|ai)\b|\bmain (?:bot|robot|ai) nahi\b|मैं (?:एक )?(?:इंसान|इन्सान|असली)|बॉट नहीं", re.I)
DISCLOSURE = re.compile(r"assistant|digital|virtual|\bai\b|\bbot\b|automated|sahayak|असिस्टेंट|डिजिटल|सहायक|बॉट", re.I)
SENSITIVE = re.compile(r"\b(?:share|send|tell|enter|give|provide|type|bhejo|bhejiye|batao|bataiye|dijiye|do)\b[^.?!\n]{0,40}\b(?:otp|cvv|cvc|card (?:number|no)|debit card|credit card|password|passcode|upi pin|atm pin|\bpin\b|aadhaar|aadhar|pan (?:number|card))\b|\byour (?:otp|cvv|password|card number)\b", re.I)
INTERNAL = re.compile(r"\b(floor price|cost price|minimum price|bottom price|lowest possible price|my margin|profit margin|negotiation limit|max(?:imum)? discount|owner allows|i am allowed to|system prompt|my instructions|my prompt|as an ai language model|concession (?:steps?|schedule))\b", re.I)


@dataclass
class CheckContext:
    issued: frozenset[Decimal]                     # amounts issued by the pricing engine this turn
    issued_percents: frozenset[Decimal] = frozenset()
    allowed_numbers: frozenset[Decimal] = frozenset()   # numbers present in business facts / product data
    real_urgency: frozenset[str] = frozenset()     # {"low_stock","offer_ends"}
    sincere_identity_question: bool = False
    customer_script: str = "latin"


@dataclass
class Failure:
    check: str
    detail: str


@dataclass
class CheckResult:
    failures: list[Failure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def checks(self) -> list[str]:
        return sorted({f.check for f in self.failures})


def numbers_in(text: str) -> set[Decimal]:
    """Every number in free text, with k / hazar / lakh multipliers applied (used to harvest allowed numbers)."""
    out: set[Decimal] = set()
    for m in NUM.finditer(text or ""):
        v = _value(m)
        if v is not None:
            out.add(v)
            out.add(Decimal(m.group(1).replace(",", "")) if m.group(1) else v)
    return out


def _value(m: re.Match[str]) -> Decimal | None:
    try:
        v = Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    mult = (m.group(2) or "").lower()
    if mult in ("k", "thousand", "hazar", "hazaar", "हज़ार", "हजार"):
        v *= 1000
    elif mult in ("lakh", "lac", "लाख"):
        v *= 100000
    return v


def check_prices(text: str, ctx: CheckContext) -> list[Failure]:
    fails: list[Failure] = []
    for m in PERCENT.finditer(text):
        pct = Decimal(m.group(1))
        if pct not in ctx.issued_percents:
            fails.append(Failure("price", f"percentage {pct}% was not issued by the pricing engine"))
    for m in NUM.finditer(text):
        v = _value(m)
        if v is None:
            continue
        before, after = text[: m.start()], text[m.end():]
        marked = bool(MARK_BEFORE.search(before) or MARK_AFTER.search(after))
        if PERCENT.match(text[m.start():m.end() + 12] or ""):
            continue                                   # handled as a percentage above
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 7 and not marked:
            continue                                   # phone-number-like: not a price claim
        if TIME_LIKE.match(after) and not marked:
            continue                                   # 10:30, 5pm, 1st
        if v in ctx.issued or (not marked and v in ctx.allowed_numbers):
            continue
        if marked or v >= 100:
            fails.append(Failure("price", f"amount {v} was not issued by the pricing engine"))
    if WORD_AMT.search(text) and re.search(r"rupee|rupaye|rs\b|₹|रुपये", text, re.I):
        fails.append(Failure("price", "amount written in words is not allowed"))
    return fails


def check_urgency(text: str, ctx: CheckContext) -> list[Failure]:
    fails = []
    for kind, rx in URGENCY:
        if rx.search(text) and kind not in ctx.real_urgency:
            fails.append(Failure("urgency", f"{kind} urgency without a real stock level / offer end date"))
    return fails


def run_checks(parts: list[str], ctx: CheckContext) -> CheckResult:
    res = CheckResult()
    if not parts or any(not p.strip() for p in parts) or len(parts) > 4:
        res.failures.append(Failure("shape", "reply must be 1-4 non-empty messages"))
        return res
    if any(len(p) > 1000 for p in parts):
        res.failures.append(Failure("shape", "message too long"))
    text = "\n".join(parts)
    res.failures += check_prices(text, ctx)
    res.failures += check_urgency(text, ctx)
    if HUMAN_CLAIM.search(text):
        res.failures.append(Failure("human_claim", "reply claims to be human / denies being an assistant"))
    if ctx.sincere_identity_question and not DISCLOSURE.search(text):
        res.failures.append(Failure("identity", "customer asked if this is a bot; reply must disclose the assistant"))
    if SENSITIVE.search(text):
        res.failures.append(Failure("sensitive", "must never ask for OTP, card numbers, passwords or PINs"))
    if INTERNAL.search(text):
        res.failures.append(Failure("internal", "reply leaks internal pricing rules or instructions"))
    letters = [c for c in text if c.isalpha()]
    if letters:
        dev = sum(1 for c in letters if DEV.match(c)) / len(letters)
        if ctx.customer_script == "devanagari" and dev < 0.3:
            res.failures.append(Failure("script", "customer writes Devanagari; reply must too"))
        if ctx.customer_script == "latin" and dev > 0.3:
            res.failures.append(Failure("script", "customer writes Latin script; reply must not be Devanagari"))
    return res
