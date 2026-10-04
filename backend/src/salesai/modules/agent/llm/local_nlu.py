"""Local deterministic planner (the stand-in for the LLM `planner` stage). Rule-based understanding of
English / Hindi / Hinglish sales conversations. Selected by LLM_PROVIDER=local; swapping to a real model
changes one environment variable. It produces the same structured PlannerOutput contract."""
from __future__ import annotations

import difflib
import re
from decimal import Decimal, InvalidOperation
from typing import Any

DEV = re.compile(r"[ऀ-ॿ]")
HINGLISH = {
    "kya", "hai", "hain", "nahi", "nahin", "chahiye", "chahie", "kitna", "kitne", "kitni", "bhaiya", "bhai", "didi", "aap", "apka", "aapka",
    "mujhe", "mereko", "kaise", "thoda", "kam", "dikhao", "dikha", "bata", "batao", "bataiye", "kal", "aaj", "ji", "haan", "han", "theek", "thik",
    "accha", "achha", "acha", "kahan", "kaha", "kab", "mein", "ka", "ki", "ke", "ko", "se", "par", "pe", "lena", "lunga", "lungi", "dena",
    "milega", "milegi", "paas", "wala", "wali", "abhi", "sirf", "bahut", "jaldi", "pakka", "daam", "kimat", "kimmat",
    "namaste", "namaskar", "dhanyavad", "shukriya", "aaunga", "aata", "aati", "karna", "karo", "kar", "bhej", "bhejo", "hoga", "hogi", "tha",
    "thi", "raha", "rahi", "wapas", "paisa", "malik", "dukaan", "dukan", "kharidna",
}
STOP = {"the", "and", "for", "you", "any", "can", "have", "has", "with", "this", "that", "what", "which", "show", "your", "price", "cost", "rate",
        "available", "stock", "want", "need", "please", "pls", "hello", "hii", "hey", "how", "much", "much?", "are", "there", "kya", "hai", "ka", "ki", "ke", "me", "mein", "se", "ko", "aur", "yeh", "ye", "wo", "hain", "kitna", "aap", "mujhe", "chahiye",
        "dikhao", "batao", "bata", "karo", "buy", "order", "take", "get", "give", "send", "details", "detail", "about"}


def norm(t: str | None) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def detect_lang(texts: list[str], prev: tuple[str, str] | None = None) -> tuple[str, str]:
    joined = " ".join(texts)
    letters = [c for c in joined if c.isalpha()]
    if not letters:
        return prev or ("en", "latin")
    dev = sum(1 for c in letters if DEV.match(c))
    if dev / len(letters) > 0.3:
        return "hi", "devanagari"
    words = set(re.findall(r"[a-z]+", joined.lower()))
    hits = len(words & HINGLISH)
    if hits >= 2 or (hits >= 1 and len(words) <= 3 and "ji" not in words):
        return "hinglish", "latin"
    if hits == 1 and prev and prev[0] == "hinglish":
        return "hinglish", "latin"
    if hits == 0 and len(words) <= 2 and prev:
        return prev
    return "en", "latin"


_WORD_NUM = {"ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "panch": 5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|hazar|hazaar|हज़ार|हजार|lakh|lac|लाख)?(?![\w])", re.I)


def extract_numbers(text: str) -> list[Decimal]:
    out: list[Decimal] = []
    for m in _NUM.finditer(text):
        try:
            v = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            continue
        mult = (m.group(2) or "").lower()
        if mult in ("k", "thousand", "hazar", "hazaar", "हज़ार", "हजार"):
            v *= 1000
        elif mult in ("lakh", "lac", "लाख"):
            v *= 100000
        out.append(v)
    return out


# Everyday Hindi retail nouns the stand-in understands in Devanagari (a real model needs no list; owners can also add
# aliases to a product). Each maps to the Latin words catalogs usually use.
HINDI_LEXICON = {
    "चावल": "rice basmati chawal", "दाल": "dal toor", "तेल": "oil tel", "साड़ी": "saree sari", "साडी": "saree sari",
    "कुर्ता": "kurta", "लहंगा": "lehenga", "फोन": "phone mobile", "मोबाइल": "mobile phone", "कवर": "cover case", "आटा": "atta flour",
    "चीनी": "sugar", "नमक": "salt", "दूध": "milk", "सूट": "suit", "दुपट्टा": "dupatta", "ब्लाउज": "blouse",
}


def tokens(s: str) -> list[str]:
    s = s.lower()
    for hi, latin in HINDI_LEXICON.items():
        if hi in s:
            s += " " + latin
    return [w for w in re.findall(r"[a-z0-9ऀ-ॿ]+", s) if len(w) >= 3 and w not in STOP]


def match_variants(text: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Variants whose product/variant/category/aliases overlap the customer's words (fuzzy for typos)."""
    toks = set(tokens(text))
    if not toks:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for c in candidates:
        hay_words: set[str] = set()
        for part in (c.get("product_name"), c.get("category"), c.get("description")):
            hay_words |= set(tokens(part or ""))
        for a in (c.get("product_attributes") or {}).get("aliases", []) or []:
            hay_words |= set(tokens(str(a)))
        name_words = set(tokens(c.get("product_name") or ""))
        vwords = set(tokens(c.get("variant_name") or "")) | {str(v).lower() for v in (c.get("attributes") or {}).values() if isinstance(v, str)}
        score = 0.0
        for t in toks:
            if t in name_words:
                score += 2
            elif t in hay_words:
                score += 1
            elif any(difflib.SequenceMatcher(None, t, h).ratio() >= 0.84 for h in name_words | hay_words if abs(len(t) - len(h)) <= 2):
                score += 1.5
            if t in vwords:
                score += 0.5
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda s: -s[0])
    if not scored:
        return []
    top = scored[0][0]
    return [c for s, c in scored if s >= top * 0.75][:3]


def pick_variant(text: str, cands: list[dict[str, Any]], matched: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not matched:
        return None
    toks = set(tokens(text))
    # same product, several variants: the one the customer named (e.g. "the red one"), else the product's default
    pid = matched[0]["product_id"]
    group = [c for c in cands if c["product_id"] == pid]
    for c in group:
        vw = set(tokens(c.get("variant_name") or "")) | {str(v).lower() for v in (c.get("attributes") or {}).values() if isinstance(v, str)}
        if toks & vw:
            return c
    return next((c for c in group if c.get("is_default")), next((c for c in group if c.get("variant_name") == "default"), group[0]))


# ---- intents ---------------------------------------------------------------------------------
def _rx(*pats: str) -> re.Pattern[str]:
    return re.compile("|".join(pats), re.I)


R_IDENTITY = _rx(r"\b(are|r) (you|u) (a )?(bot|robot|ai|human|real|person|machine|insaan|chatbot|automated)", r"\bis this (a )?(bot|ai|robot|human|real person|automated)",
                 r"\b(bot|robot|ai) (ho|hai|hain)\b", r"\bkya (aap|tum) (insaan|bot|robot|real|human)", r"(आप|तुम) (बॉट|रोबोट|इंसान|असली)", r"\b(human|insaan|real person)\??$",
                 r"\bam i (talking|speaking|chatting) (to|with) (a )?(bot|human|person|ai|real)", r"\btalking to (a )?(bot|human|person|ai|real)")
R_HUMAN = _rx(r"\b(talk|speak|connect|call|chat) (to|with) (the |your )?(owner|human|person|manager|someone|agent|staff|real)", r"\b(owner|malik|manager|boss) (se|ko) (baat|bulao|connect)",
              r"\bwant (the |to speak to )?(owner|human|a person|manager)", r"\bcall me\b", r"(मालिक|ओनर) (से|को)", r"\binsaan se\b", r"\bhuman (please|agent)", r"\bowner (please|chahiye)")
R_COMPLAINT = _rx(r"\b(complaint|complain|refund|damaged|defective|broken|torn|wrong (item|product|size|colou?r)|not (received|delivered|working)|never (came|arrived)|cheat|fraud|scam|worst|pathetic|bad quality|poor quality|disappointed|kharab|dhokha|dhoka|paisa wapas|money back|late delivery|fake)\b",
                  r"(शिकायत|धोखा|खराब|पैसे वापस|रिफंड|नकली)")
R_NOTINT = _rx(r"\bnot interested\b", r"\bno thanks?\b", r"\bno thank you\b", r"\bnahi chahiye\b", r"\bnahin chahiye\b", r"\bdon'?t want\b", r"\bdo not want\b", r"\bleave me alone\b", r"\bstop (messaging|texting|bothering)\b", r"(रुचि नहीं|नहीं चाहिए|मत भेजो)", r"\bmat bhejo\b", r"\bnot now\b")
R_BYE = _rx(r"^(ok(ay)?[, ]*)?(thanks?|thank you)?[, ]*(bye+|goodbye|good night|see you|tata|alvida|chalta hu|ok bye)\b", r"\b(bye+|goodbye|tata|alvida)\b", r"(अलविदा|फिर मिलेंगे)")
R_THANKS = _rx(r"^(ok(ay)?[, ]*)?(thanks?|thank you|thx|ty|shukriya|dhanyavad|dhanyawad|great thanks)\b", r"(धन्यवाद|शुक्रिया)")
R_ORDER = _rx(r"\b(i('| wi)?ll|i will|i wanna|i want to|want to|would like to|like to|going to|gonna|let'?s) (buy|order|take|book|purchase|get)\b", r"\b(i('| wi)?ll|i will) take (it|this|that|them|one|two|\d+)\b",
              r"\b(place|make|confirm) (an? |my |the )?order\b", r"\bbook (it|this|that)\b", r"\bsend (it|this|me|one)\b",
              r"\b(lena hai|le lunga|le lungi|le lenge|lunga|lungi|order karna|order kar(o| do)|book kar(o| do)|kharidna|khareedna|bhej do|bhejdo|pack kar)\b", r"(खरीदना|ऑर्डर|ले लूंगा|ले लूँगा|लेना है|भेज दो|बुक कर)")
R_VISIT = _rx(r"\b(visit|come to|coming to|drop by|stop by) (the |your )?(shop|store|showroom|outlet|place)\b", r"\b(shop|store|showroom|dukaan|dukan) (par|pe|me|mein) (aa|aunga|aaunga|aaungi|aata|aati)\b",
              r"\b(aa|aaunga|aaungi|aata hu|aati hu|aa raha|aa rahi) (hu|hoon|hun)?\b.*(shop|store|dukaan|kal|aaj|sunday|monday|tuesday|wednesday|thursday|friday|saturday)", r"\bcan i (come|see|visit|check)\b.*(shop|store|in person|physically)", r"\bvisit\b", r"(दुकान|शोरूम) (पर|में) आ")
R_AFFIRM = _rx(r"^(yes+|yeah|yep|yup|ok(ay)?|sure|confirm(ed)?|done|haan|ha|haa|han|theek hai|thik hai|theek|pakka|kar do|kardo|ji|ji haan|bilkul|go ahead|proceed|correct|right)[\s.!,]*(please|pls|ji|confirm(ed)?|kar do|hai)?[\s.!]*$", r"^(हाँ|हां|ठीक है|जी|बिल्कुल|पक्का|कर दो)")
R_DISCOUNT = _rx(r"\b(discount|less|reduce|reduction|cheaper|lower|bargain|negotiat\w*|best price|last price|final price|lowest|better price|best (you|u) can|what'?s (the |your )?best|best possible|best rate|your best|(any|koi|current|special|festival|diwali|ongoing) offers?|concession|can you do (better|less|something)|thoda kam|kam kar|kam karo|kam ho|rate kam|price kam|kuch kam|thodi chhoot|chhoot|chut|sasta|sasti)\b",
                 r"(छूट|कम कर|कम करो|थोड़ा कम|सस्ता|डिस्काउंट|ऑफर)")
R_COUNTER_WORDS = _rx(r"\b(can you do|can u do|will you do|do it for|for|only|just|final|pay|give|offer|budget|mein de|me de|mein dedo|me dedo|tak|me chalega|mein chalega|dedo|de do|ok at|settle)\b", r"(में दे|में दो|तक|चलेगा)")
R_PRICE = _rx(r"\b(price|cost|rate|rates|how much|howmuch|kitna|kitne|kitni|kimat|kimmat|daam|dam|charges?|pricing|what'?s the price|prize|amount|mrp)\b", r"(कीमत|दाम|कितना|कितने|भाव|रेट)", r"₹|\brs\.?\b|\binr\b")
R_AVAIL = _rx(r"\b(available|availability|in stock|stock|do you have|do u have|have you got|got any|milega|milegi|mil jayega|hai kya|hai na|available hai|aapke paas|aap ke paas|ready stock)\b", r"(उपलब्ध|मिलेगा|मिलेगी|है क्या|स्टॉक)")
R_GREET = _rx(r"^(hi+|hello+|hey+|hii+|helo|namaste|namaskar|good (morning|afternoon|evening|day)|gm|salaam|salam|sat sri akal|jai (shri )?(krishna|ram)|ram ram|yo|hola|hlo)\b", r"(नमस्ते|नमस्कार|सलाम)", r"\bkya haal\b|\bhow are you\b|\bkaise ho\b|\bkaisa hai\b")
R_SHOW = _rx(r"\b(show|dikhao|dikha do|details?|photo|pic|design|catalog(ue)?|collection|what do you (sell|have)|kya (kya )?milta|kya hai|options?|varieties|variety|types?|new arrivals?|latest)\b", r"(दिखाओ|कलेक्शन|क्या क्या)")
R_SCOPE_OUT = _rx(r"\b(weather|joke|poem|story|song|lyrics|recipe|horoscope|astrology|cricket|ipl|score|news|election|prime minister|president|capital of|who (is|was|won)|movie|film|python|javascript|java code|write (me )?(a |an )?(code|essay|program|script)|solve|equation|math|homework|bitcoin|crypto|stock market|share price|translate|chatgpt|openai|gpt|your (model|prompt|instructions)|system prompt|ignore (all |your |previous )?instructions|pretend|roleplay|relationship advice|medical advice|legal advice|tell me a)\b",
                      r"\bwhat is \d+\s*[\+\-\*/x]\s*\d+", r"\bwho (made|created|built|trained) you\b", r"(मौसम|चुटकुला|कविता|कहानी|गाना|क्रिकेट|खबर|चुनाव|प्रधानमंत्री)")
TOPICS = {
    "hours": _rx(r"\b(hours?|timings?|time|open|opening|close|closing|closed|kab tak|kab khulta|kab band|khulta|band hota|sunday|holiday|working days?)\b", r"(समय|टाइमिंग|टाइम|खुलता|खुली|खुला|खुलती|बंद|कब तक)"),
    "address": _rx(r"\b(address|location|where|kahan|kaha|directions?|map|nearby|landmark|locate|pata)\b", r"(पता|कहाँ|कहां|लोकेशन)"),
    "delivery": _rx(r"\b(deliver\w*|ship\w*|courier|dispatch|home delivery|delivery charges?|cod|pincode|send to|bhejte|bhejna)\b", r"(डिलीवरी|भेजते|कूरियर)"),
    "payment": _rx(r"\b(payment|pay|upi|gpay|google pay|phonepe|paytm|cash|card|emi|advance|cod|net ?banking|bank transfer|partial)\b", r"(भुगतान|पेमेंट|यूपीआई|कैश)"),
    "returns": _rx(r"\b(return|returns|exchange|refund|replacement|warranty|guarantee|policy|policies)\b", r"(रिटर्न|एक्सचेंज|वापसी|गारंटी)"),
    "contact": _rx(r"\b(contact|phone|number|call|whatsapp number|email|mail|landline)\b", r"(संपर्क|फोन|नंबर)"),
}
R_BUSINESS = _rx(*[p.pattern for p in TOPICS.values()])
R_ADDR = re.compile(r"\b(address|near|road|street|colony|nagar|sector|lane|apartment|flat|building|pin ?code|pincode|society|marg|chowk|bazaar|market)\b|\b\d{6}\b", re.I)
R_QTY = re.compile(r"\b(\d{1,4})\s*(pcs?|pieces?|piece|nos?|units?|sets?|sarees?|saris?|kurtas?|items?|bottles?|packs?|boxes?)\b|\bx\s?(\d{1,3})\b|\b(\d{1,3})\s?x\b", re.I)
R_VISIT_TIME = re.compile(r"\b(today|tonight|tomorrow|tmrw|kal|aaj|parso|monday|tuesday|wednesday|thursday|friday|saturday|sunday|this (?:weekend|evening|morning|afternoon)|next (?:week|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b(?:\s+(?:at|around|by|ko)?\s*(\d{1,2}(?::\d{2})?\s?(?:am|pm)?|morning|evening|afternoon))?", re.I)
R_TIME = re.compile(r"\b(today|tonight|tomorrow|tmrw|kal|aaj|parso|day after|this (morning|evening|afternoon|weekend)|next (week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(:\d{2})?\s?(am|pm)|morning|evening|afternoon|subah|shaam|dopahar)\b", re.I)
OCCASION = re.compile(r"\b(wedding|marriage|shaadi|engagement|birthday|gift|diwali|festival|party|anniversary|office|daily wear|puja|baby shower|reception)\b", re.I)

STAGE_ORDER = ["new", "exploring", "interested", "negotiating", "ready_to_buy", "won", "lost"]


def _stage_max(a: str, b: str) -> str:
    if "won" in (a, b):
        return "won"
    if a == "lost" and b != "lost":
        return b if STAGE_ORDER.index(b) >= 1 else a
    return a if STAGE_ORDER.index(a) >= STAGE_ORDER.index(b) else b


def plan(inp: dict[str, Any]) -> dict[str, Any]:
    conv = inp["conversation"]
    cands: list[dict[str, Any]] = inp["candidates"]
    unanswered: list[dict[str, Any]] = inp["unanswered"]
    qual: dict[str, Any] = conv.get("qualification") or {}
    prev_stage = conv.get("lead_stage", "new")
    texts = [m.get("text") or "" for m in unanswered if m.get("text")]
    text = norm(" . ".join(texts))
    hist_langs = [m["body"] for m in conv.get("history", []) if m.get("sender") == "customer" and m.get("body")][-3:]
    prev_lang = detect_lang(hist_langs) if hist_langs else None
    lang, script = detect_lang(texts, prev_lang) if texts else (prev_lang or ("en", "latin"))
    kinds = {m.get("kind") for m in unanswered}
    out: dict[str, Any] = {"language": lang, "script": script, "formality": "casual" if len(text) < 25 else "neutral", "lead_stage": prev_stage}

    def done(intent: str, **kw: Any) -> dict[str, Any]:
        out.update(intent=intent, **kw)
        return out

    if inp.get("trigger") == "nudge":
        return done("nudge", action="reply")

    if not text and kinds & {"audio", "image", "other"}:
        return done("unsupported_media", action="reply")

    focus = qual.get("focus_variant_id")
    matched = match_variants(text, cands)
    variant = pick_variant(text, cands, matched)
    if variant is None and focus:
        variant = next((c for c in cands if str(c["variant_id"]) == str(focus)), None)
    out["greeted"] = bool(R_GREET.search(text)) and len(text) < 60 or bool(re.match(R_GREET, text))
    numbers = extract_numbers(text)
    qm = R_QTY.search(text)
    qty = int(next(g for g in qm.groups() if g and g.isdigit())) if qm else int(qual.get("quantity") or 1)
    q_upd: dict[str, Any] = {}
    if qm:
        q_upd["quantity"] = qty
    if (m := OCCASION.search(text)):
        q_upd["occasion"] = m.group(1).lower()
    if (m := R_TIME.search(text)) and not R_VISIT.search(text):
        q_upd["timeline"] = m.group(0).lower()
    is_addr = bool(R_ADDR.search(text)) and (len(text) > 18 or bool(re.search(r"\b\d{6}\b", text)))
    if is_addr and qual.get("pending_order"):
        q_upd["delivery_address"] = " ".join(texts).strip()[:300]
    out["qualification"] = q_upd

    def refs(ask: str = "none", counter: Decimal | None = None) -> list[dict[str, Any]]:
        if variant is None:
            return []
        return [{"variant_id": str(variant["variant_id"]), "quantity": qty, "counter_price": str(counter) if counter is not None else None,
                 "advance_payment": bool(re.search(r"\b(advance|upfront|prepay|pay now|pay today|pay in advance)\b", text))}]

    def stage(s: str) -> str:
        return _stage_max(prev_stage, s)

    # --- safety / control intents first
    if R_IDENTITY.search(text):
        return done("identity_question", action="reply", sincere_identity_question=True, lead_stage=prev_stage)
    if R_HUMAN.search(text):
        return done("human_request", action="handoff", wants_human=True, handoff_reason="customer_asked_human", lead_stage=prev_stage)
    if R_COMPLAINT.search(text):
        return done("complaint", action="handoff", complaint=True, handoff_reason="complaint", lead_stage=prev_stage)
    if R_NOTINT.search(text):
        return done("not_interested", action="reply", selling_stopped=True, lead_stage="lost")
    scope_out = bool(R_SCOPE_OUT.search(text))
    if scope_out and variant is None:
        return done("out_of_scope", action="reply")

    # --- commitments in flight
    pending = qual.get("pending_order")
    if R_AFFIRM.match(text) and pending:
        if qual.get("delivery_address") or qual.get("pickup"):
            return done("affirmation", action="reply", lead_stage="ready_to_buy",
                        commitment={"kind": "order", "variant_id": pending["variant_id"], "quantity": pending.get("quantity", 1),
                                    "delivery_address": qual.get("delivery_address"), "confirmed": True})
        return done("affirmation", action="reply", lead_stage="ready_to_buy",
                    commitment={"kind": "order", "variant_id": pending["variant_id"], "quantity": pending.get("quantity", 1), "confirmed": False})
    if pending and is_addr:
        return done("order_intent", action="reply", lead_stage="ready_to_buy",
                    commitment={"kind": "order", "variant_id": pending["variant_id"], "quantity": pending.get("quantity", 1),
                                "delivery_address": q_upd.get("delivery_address"), "confirmed": False})
    if R_VISIT.search(text):
        tm = R_VISIT_TIME.search(text) or R_TIME.search(text)
        return done("visit_intent", action="reply", lead_stage=stage("ready_to_buy"),
                    commitment={"kind": "visit", "visit_time": " ".join(tm.group(0).split()) if tm else None, "confirmed": bool(tm)},
                    product_refs=refs())
    if R_ORDER.search(text):
        return done("order_intent", action="reply", lead_stage=stage("ready_to_buy"), product_refs=refs(),
                    commitment={"kind": "order", "variant_id": str(variant["variant_id"]) if variant else None, "quantity": qty,
                                "delivery_address": q_upd.get("delivery_address") or qual.get("delivery_address"), "confirmed": False})

    # --- price / negotiation
    has_price_word = bool(R_PRICE.search(text))
    has_discount = bool(R_DISCOUNT.search(text))
    counter_nums = [n for n in numbers if n >= 10 and not (qm and n == qty)]
    if counter_nums and variant is not None and (qual.get("price_quoted") or has_discount) and not R_ADDR.search(text) and (R_COUNTER_WORDS.search(text) or has_discount or len(text) < 24):
        return done("counter_offer", action="reply", ask="counter", lead_stage=stage("negotiating"), product_refs=refs("counter", counter_nums[-1]))
    if has_discount and (variant is not None or qual.get("price_quoted")):
        return done("discount_request", action="reply", ask="discount", lead_stage=stage("negotiating"), product_refs=refs("discount"))
    if has_price_word and (variant is not None):
        return done("price_query", action="reply", ask="price", lead_stage=stage("interested"), product_refs=refs("price"))
    if has_price_word or has_discount:
        return done("price_query", action="reply", ask="price", lead_stage=prev_stage, product_refs=refs("price"))   # no product known: writer asks which

    # --- information
    topics = [t for t, rx in TOPICS.items() if rx.search(text)]
    if R_AVAIL.search(text) and (variant is not None or not topics):
        return done("availability", action="reply", lead_stage=stage("exploring" if variant is None else "interested"), product_refs=refs())
    if topics and variant is None:
        return done("business_info", action="reply", info_topics=topics, lead_stage=stage("exploring"))
    if R_GREET.search(text) and not matched and not topics:
        if R_SHOW.search(text) or len(text) > 40:
            pass
        else:
            return done("greeting", action="reply", lead_stage=stage("exploring"), greeted=True)
    if R_BYE.search(text) and not matched:
        return done("goodbye", action="reply", lead_stage=prev_stage)
    if R_THANKS.search(text) and not matched:
        return done("thanks", action="reply", lead_stage=prev_stage, reaction_emoji="🙏" if len(text) < 14 else None)
    if variant is not None or R_SHOW.search(text):
        return done("product_inquiry", action="reply", lead_stage=stage("interested" if variant else "exploring"), product_refs=refs(), info_topics=topics)
    if R_AFFIRM.match(text):
        return done("affirmation", action="reply", lead_stage=prev_stage)
    if scope_out:
        return done("out_of_scope", action="reply")
    if re.search(r"\b(how are you|kaise ho|kaisa hai)\b", text):
        return done("smalltalk", action="reply")
    if "?" in text or re.search(r"\b(kya|kaise|kyun|kab|kaun|kitna|what|why|when|how|which|who|can|do|does|is|are)\b", text):
        return done("unknown", action="handoff", handoff_reason="unknown_answer", knowledge_question=" ".join(texts).strip()[:400])
    return done("unknown", action="reply")
