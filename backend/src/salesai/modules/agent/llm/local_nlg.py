"""Local deterministic writer (the stand-in for the LLM `writer` stage). Renders directives — already
decided by code — into short WhatsApp replies in English, Hinglish (Hindi in Latin script) or Hindi
(Devanagari), mirroring the customer. It can only print numbers that appear in the directives."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

L = ("en", "hinglish", "hi")


def money(amount: str | Decimal, currency: str = "INR") -> str:
    d = Decimal(str(amount))
    sign = "₹" if currency == "INR" else f"{currency} "
    whole, _, frac = f"{d:.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join([*parts, tail])
    return f"{sign}{whole}" + (f".{frac}" if frac != "00" else "")


def pick(lang: str, en: str, hing: str, hi: str) -> str:
    return {"en": en, "hinglish": hing, "hi": hi}.get(lang, en)


def vmap(values: list[dict[str, Any]]) -> dict[str, str]:
    return {v["kind"]: v["amount"] for v in values}


def cond_text(lang: str, c: str) -> str:
    if c.startswith("quantity>="):
        n = c.split(">=")[1]
        return pick(lang, f"you take {n} or more", f"aap {n} ya usse zyada lein", f"आप {n} या उससे ज़्यादा लें")
    if c == "advance_payment":
        return pick(lang, "you pay in advance", "aap advance payment karein", "आप एडवांस पेमेंट करें")
    if c == "repeat_customer":
        return pick(lang, "you're a returning customer", "aap pehle bhi kharid chuke hon", "आप पहले भी खरीद चुके हों")
    return c


def name_part(lang: str, d: dict[str, Any]) -> str:
    n = d.get("customer_name")
    hon = d.get("honorific") or ""
    if not n or n.lower() in ("customer",):
        return ""
    return f" {n}" + (f" {hon}" if hon and lang != "en" else "")


def render(d: dict[str, Any], lang: str, ctx: dict[str, Any]) -> str:
    t = d["type"]
    biz = ctx.get("business_name") or "our shop"
    cur = d.get("currency", "INR")
    prod = d.get("product") or ""
    var = d.get("variant")
    pv = f"{prod} ({var})" if var and var != "default" else prod

    if t == "greeting" and d.get("short"):
        nm = name_part(lang, {**ctx, **d})
        return pick(lang, f"Hello{nm}!", f"Namaste{nm}!", f"नमस्ते{nm}!")
    if t == "ack":
        return pick(lang, "Got it! Let me know if there's anything else you'd like to know.", "Theek hai! Aur kuch jaanna ho to bataiye.", "ठीक है! और कुछ जानना हो तो बताइए।")
    if t == "catalog":
        items = ", ".join(d.get("items", []))
        return pick(lang, f"Here's some of what we have: {items}. Which one would you like to know more about?",
                    f"Hamare paas yeh sab hai: {items}. Aap kis ke baare mein jaanna chahenge?", f"हमारे पास यह सब है: {items}। आप किसके बारे में जानना चाहेंगे?")
    if t == "greeting":
        nm = name_part(lang, {**ctx, **d})
        return pick(lang, f"Hello{nm}! Welcome to {biz}. How can I help you today?",
                    f"Namaste{nm}! {biz} mein aapka swagat hai. Bataiye, main aapki kya madad kar sakta hoon?",
                    f"नमस्ते{nm}! {biz} में आपका स्वागत है। बताइए, मैं आपकी क्या मदद कर सकता हूँ?")
    if t == "thanks":
        return pick(lang, "You're welcome! Let me know if you need anything else.", "Aapka swagat hai! Aur kuch chahiye to bataiye.", "आपका स्वागत है! और कुछ चाहिए तो बताइए।")
    if t == "goodbye":
        return pick(lang, f"Thank you for visiting {biz}! Have a great day.", f"{biz} aane ke liye dhanyavad! Aapka din shubh ho.", f"{biz} आने के लिए धन्यवाद! आपका दिन शुभ हो।")
    if t == "not_interested":
        return pick(lang, "No problem at all! If you need anything later, just message us. Thank you!",
                    "Koi baat nahi! Kabhi zaroorat ho to bas message kar dijiye. Dhanyavad!", "कोई बात नहीं! कभी ज़रूरत हो तो बस मैसेज कर दीजिए। धन्यवाद!")
    if t == "smalltalk":
        return pick(lang, f"I'm doing well, thank you! I'm {biz}'s assistant — happy to help with our products. What are you looking for?",
                    f"Main theek hoon, shukriya! Main {biz} ka assistant hoon. Aap kya dhoondh rahe hain?", f"मैं ठीक हूँ, धन्यवाद! मैं {biz} का असिस्टेंट हूँ। आप क्या ढूँढ रहे हैं?")
    if t == "identity":
        return pick(lang, f"I'm {biz}'s digital assistant, not a person. I can help with products, prices and orders, and I can connect you with the owner anytime — just say so.",
                    f"Main {biz} ka digital assistant hoon, koi insaan nahi. Products, price aur orders mein madad kar sakta hoon, aur jab bhi aap kahein owner se connect kara dunga.",
                    f"मैं {biz} का डिजिटल असिस्टेंट हूँ, कोई इंसान नहीं। मैं प्रोडक्ट, कीमत और ऑर्डर में मदद कर सकता हूँ, और आप कहें तो मालिक से भी जोड़ सकता हूँ।")
    if t == "decline_out_of_scope":
        return pick(lang, f"Sorry, I can only help with {biz}'s products and orders. Is there something from our collection you'd like to know about?",
                    f"Maaf kijiye, main sirf {biz} ke products aur orders mein madad kar sakta hoon. Hamare products ke baare mein kuch poochna hai?",
                    f"माफ़ कीजिए, मैं सिर्फ़ {biz} के प्रोडक्ट और ऑर्डर में मदद कर सकता हूँ। हमारे प्रोडक्ट के बारे में कुछ पूछना है?")
    if t == "unsupported_media":
        return pick(lang, "I can't open voice notes or images yet — could you please type your message?",
                    "Main abhi voice note ya image nahi khol sakta — kya aap likh kar bhej sakte hain?", "मैं अभी वॉइस नोट या इमेज नहीं खोल सकता — क्या आप लिखकर भेज सकते हैं?")
    if t == "ask_product":
        return pick(lang, "Sure! Which product are you asking about?", "Zaroor! Aap kis product ke baare mein pooch rahe hain?", "ज़रूर! आप किस प्रोडक्ट के बारे में पूछ रहे हैं?")
    if t == "product_info":
        desc = (d.get("description") or "").strip().rstrip(".")
        av = d.get("availability")
        extra = {"out_of_stock": pick(lang, " It is currently out of stock.", " Abhi stock mein nahi hai.", " यह अभी स्टॉक में नहीं है।"),
                 "made_to_order": pick(lang, " It is made to order.", " Yeh order par banta hai.", " यह ऑर्डर पर बनता है।")}.get(av, "")
        head = f"{pv}: {desc}." if desc else f"Yes, we have {pv}." if lang == "en" else f"{pv}: {desc}." if desc else f"Ji, {pv} available hai."
        return head + extra
    if t == "availability":
        av = d.get("availability")
        if av == "out_of_stock":
            return pick(lang, f"Sorry, {pv} is currently out of stock.", f"Maaf kijiye, {pv} abhi stock mein nahi hai.", f"माफ़ कीजिए, {pv} अभी स्टॉक में नहीं है।")
        if av == "made_to_order":
            return pick(lang, f"Yes, {pv} is available on order.", f"Ji, {pv} order par available hai.", f"जी, {pv} ऑर्डर पर उपलब्ध है।")
        return pick(lang, f"Yes, {pv} is available.", f"Ji haan, {pv} available hai.", f"जी हाँ, {pv} उपलब्ध है।")
    if t == "quote":
        return _quote(d, lang, pv, cur)
    if t == "ask_qualify":
        fields = d.get("fields") or ["quantity"]
        what = {"quantity": pick(lang, "how many you need", "kitne chahiye", "कितने चाहिए"),
                "occasion": pick(lang, "the occasion", "kis occasion ke liye", "किस मौके के लिए"),
                "delivery_location": pick(lang, "your delivery location", "delivery kahan karni hai", "डिलीवरी कहाँ करनी है")}
        parts = [what.get(f, f) for f in fields]
        joined = (" and ".join(parts) if lang == "en" else " aur ".join(parts) if lang == "hinglish" else " और ".join(parts))
        return pick(lang, f"To give you the right price for {pv}, could you tell me {joined}?",
                    f"{pv} ka sahi price batane ke liye, kya aap bata sakte hain {joined}?", f"{pv} की सही कीमत बताने के लिए, क्या आप बता सकते हैं {joined}?")
    if t == "handoff_notice":
        r = d.get("reason")
        if r == "on_request_price":
            return pick(lang, "The price for this depends on your requirements, so I've asked the owner to reach out to you with exact details.",
                        "Iska price aapki requirement par depend karta hai, isliye maine owner ko bataya hai — woh aapko exact details denge.",
                        "इसकी कीमत आपकी ज़रूरत पर निर्भर करती है, इसलिए मैंने मालिक को बताया है — वे आपको सही जानकारी देंगे।")
        if r in ("below_floor", "owner_review"):
            return pick(lang, "Let me check this with the owner and get back to you shortly.", "Main isko owner se check karke jaldi aapko batata hoon.", "मैं इसे मालिक से पूछकर जल्दी आपको बताता हूँ।")
        if r == "customer_asked_human":
            return pick(lang, "Of course! I've let the owner know — they'll reply to you personally very soon.", "Bilkul! Maine owner ko bata diya hai — woh jaldi aapko khud reply karenge.",
                        "बिल्कुल! मैंने मालिक को बता दिया है — वे जल्दी आपको खुद जवाब देंगे।")
        if r == "complaint":
            return pick(lang, "I'm really sorry about this. I've informed the owner and they will get in touch with you shortly.", "Iske liye mujhe bahut khed hai. Maine owner ko bata diya hai, woh jaldi aapse sampark karenge.",
                        "इसके लिए मुझे बहुत खेद है। मैंने मालिक को बता दिया है, वे जल्दी आपसे संपर्क करेंगे।")
        if r == "unknown_answer":
            return pick(lang, "Good question — I'm not sure about that. Let me check with the owner and get back to you.", "Achha sawaal hai — mujhe iska pakka jawab nahi pata. Main owner se poochkar aapko batata hoon.",
                        "अच्छा सवाल है — मुझे इसका पक्का जवाब नहीं पता। मैं मालिक से पूछकर आपको बताता हूँ।")
        if r == "system_failure":
            return pick(lang, "Thanks for your message! I'm checking with the owner and will get back to you shortly.", "Aapke message ke liye dhanyavad! Main owner se check karke jaldi reply karta hoon.",
                        "आपके मैसेज के लिए धन्यवाद! मैं मालिक से पूछकर जल्दी जवाब देता हूँ।")
        return pick(lang, "I've shared your request with the owner, who will confirm shortly.", "Maine aapki request owner ko bhej di hai, woh jaldi confirm karenge.", "मैंने आपकी रिक्वेस्ट मालिक को भेज दी है, वे जल्दी पुष्टि करेंगे।")
    if t == "info":
        txt = str(d.get("text") or "").strip().rstrip(".")
        topic = d.get("topic")
        lead = {"hours": pick(lang, "Our timings: ", "Hamari timing: ", "हमारा समय: "), "address": pick(lang, "You can find us at ", "Hamara address: ", "हमारा पता: "),
                "payment": pick(lang, "We accept ", "Hum accept karte hain: ", "हम स्वीकार करते हैं: "), "delivery": pick(lang, "Delivery: ", "Delivery: ", "डिलीवरी: "),
                "returns": pick(lang, "Our policy: ", "Hamari policy: ", "हमारी पॉलिसी: "), "contact": pick(lang, "You can reach us at ", "Aap hume yahan contact kar sakte hain: ", "आप हमसे यहाँ संपर्क कर सकते हैं: ")}.get(topic, "")
        return f"{lead}{txt}."
    if t == "facts":
        return " ".join(str(i).strip() for i in d.get("items", []))
    if t == "order_summary":
        price, total, qty = d.get("price"), d.get("total"), d.get("quantity", 1)
        line = money(price, cur) if price else ""
        tot = f" (total {money(total, cur)})" if total and qty > 1 else ""
        need_addr = d.get("needs_address")
        if lang == "en":
            base = f"Great choice! Order summary: {qty} × {pv} at {line}{tot}."
            return base + (" Please share your delivery address, then reply 'confirm'." if need_addr else " Shall I confirm this order? Reply 'yes' to confirm.")
        if lang == "hinglish":
            base = f"Bahut badhiya! Order: {qty} × {pv}, {line}{tot}."
            return base + (" Kripya apna delivery address bhejein, phir 'confirm' likhein." if need_addr else " Kya main order confirm kar doon? 'yes' likhein.")
        base = f"बहुत बढ़िया! ऑर्डर: {qty} × {pv}, {line}{tot}।"
        return base + (" कृपया अपना डिलीवरी पता भेजें, फिर 'confirm' लिखें।" if need_addr else " क्या मैं ऑर्डर कन्फर्म कर दूँ? 'yes' लिखें।")
    if t == "order_captured":
        return pick(lang, "Thank you! I've noted your order. The owner will confirm it shortly and share the payment details.",
                    "Dhanyavad! Maine aapka order note kar liya hai. Owner jaldi confirm karke payment details bhejenge.", "धन्यवाद! मैंने आपका ऑर्डर नोट कर लिया है। मालिक जल्दी कन्फर्म करके पेमेंट की जानकारी भेजेंगे।")
    if t == "visit_ask_time":
        return pick(lang, "We'd love to see you! When would you like to visit?", "Aapka swagat hai! Aap kab aana chahenge?", "आपका स्वागत है! आप कब आना चाहेंगे?")
    if t == "visit_captured":
        tm = d.get("time") or ""
        return pick(lang, f"Noted — we'll see you {tm}. The owner will confirm shortly.", f"Theek hai — hum aapka {tm} intezaar karenge. Owner jaldi confirm karenge.", f"ठीक है — हम {tm} आपका इंतज़ार करेंगे। मालिक जल्दी कन्फर्म करेंगे।")
    if t == "nudge":
        nm = name_part(lang, {**ctx, **d})
        item = f" about the {prod}" if prod else ""
        return pick(lang, f"Hi{nm}, just checking in{item} — happy to answer any questions you have.", f"Hi{nm}, bas poochna tha{(' ' + prod + ' ke baare mein') if prod else ''} — koi sawaal ho to bataiye.",
                    f"हाय{nm}, बस पूछना था{(' ' + prod + ' के बारे में') if prod else ''} — कोई सवाल हो तो बताइए।")
    return ""


def _urgency(d: dict[str, Any], lang: str, cur: str) -> str:
    out = []
    for u in d.get("real_urgency", []):
        if u["kind"] == "low_stock":
            n = re.search(r"\d+", u["detail"])
            if n:
                out.append(pick(lang, f"Only {n.group(0)} left in stock.", f"Stock mein sirf {n.group(0)} bache hain.", f"स्टॉक में सिर्फ़ {n.group(0)} बचे हैं।"))
        elif u["kind"] == "offer_ends":
            day = u["detail"][:10]
            out.append(pick(lang, f"This offer is valid until {day}.", f"Yeh offer {day} tak valid hai.", f"यह ऑफ़र {day} तक मान्य है।"))
    return " ".join(out)


def _quote(d: dict[str, Any], lang: str, pv: str, cur: str) -> str:
    kind = d.get("decision")
    v = vmap(d.get("values", []))
    price = money(v["price"], cur) if "price" in v else ""
    qty = int(Decimal(v["quantity"])) if "quantity" in v else 1
    tot = f" {pick(lang, 'For', 'Total', 'कुल')} {qty}: {money(v['total'], cur)}." if "total" in v else ""
    offer = ""
    if d.get("applied_offers"):
        names = ", ".join(d["applied_offers"])
        offer = pick(lang, f" (with our {names} offer)", f" (hamare {names} offer ke saath)", f" (हमारे {names} ऑफ़र के साथ)")
    free = ""
    if d.get("free_items"):
        free = " " + pick(lang, "Plus a free " + ", ".join(d["free_items"]) + ".", "Saath mein free " + ", ".join(d["free_items"]) + ".", "साथ में मुफ़्त " + ", ".join(d["free_items"]) + "।")
    urg = _urgency(d, lang, cur)
    sem = d.get("semantics")
    if sem == "range":
        return pick(lang, f"{pv} is priced between {money(v['range_min'], cur)} and {money(v['range_max'], cur)}, depending on the design.",
                    f"{pv} ka price {money(v['range_min'], cur)} se {money(v['range_max'], cur)} ke beech hai, design par depend karta hai.",
                    f"{pv} की कीमत {money(v['range_min'], cur)} से {money(v['range_max'], cur)} के बीच है, डिज़ाइन पर निर्भर करता है।") + free
    if sem == "from":
        return pick(lang, f"{pv} starts from {price}.", f"{pv} {price} se shuru hota hai.", f"{pv} {price} से शुरू होता है।") + free
    body: str
    if kind == "concede":
        body = pick(lang, f"Okay, for you I can do {pv} at {price}{offer}.", f"Theek hai, aapke liye {pv} {price} mein de sakta hoon{offer}.", f"ठीक है, आपके लिए {pv} {price} में दे सकता हूँ{offer}।")
        if d.get("final_offer"):
            body += " " + pick(lang, "That's my best price.", "Yeh mera best price hai.", "यह मेरा सबसे अच्छा दाम है।")
    elif kind == "accept":
        body = pick(lang, f"Deal! {pv} at {price}{offer}.", f"Pakka! {pv} {price} mein{offer}.", f"पक्का! {pv} {price} में{offer}।")
    elif kind == "hold":
        body = pick(lang, f"I'm sorry, {price} is the lowest I can do for {pv}.", f"Maaf kijiye, {pv} ke liye {price} sabse kam hai jo main kar sakta hoon.", f"माफ़ कीजिए, {pv} के लिए {price} सबसे कम है जो मैं कर सकता हूँ।")
    elif kind == "needs_concession":
        conds = " / ".join(cond_text(lang, c) for c in d.get("unmet", []))
        body = pick(lang, f"I can look at a better price for {pv} if {conds}. Right now it's {price}.", f"Agar {conds} to main {pv} ka better price de sakta hoon. Abhi yeh {price} hai.",
                    f"अगर {conds} तो मैं {pv} का बेहतर दाम दे सकता हूँ। अभी यह {price} है।")
    elif kind == "firm":
        body = pick(lang, f"The price of {pv} is fixed at {price}{offer}.", f"{pv} ka price {price} fixed hai{offer}.", f"{pv} का दाम {price} तय है{offer}।")
    else:
        body = pick(lang, f"{pv} is {price}{offer}.", f"{pv} {price} ka hai{offer}.", f"{pv} {price} का है{offer}।")
    return (body + tot + free + (" " + urg if urg else "")).strip()


def write(inp: dict[str, Any]) -> dict[str, Any]:
    lang = inp.get("language", "en")
    ctx = {"business_name": inp.get("business_name"), "customer_name": inp.get("customer_name"), "honorific": inp.get("honorific")}
    parts: list[str] = []
    examples = {e["situation"]: e["owner_reply"] for e in inp.get("style_examples", []) if not re.search(r"\d", e.get("owner_reply", ""))}
    for d in inp.get("directives", []):
        if d["type"] in ("greeting", "thanks", "goodbye") and d["type"] in examples:
            parts.append(examples[d["type"]].strip())
            continue
        text = render({**d, "customer_name": ctx["customer_name"], "honorific": ctx["honorific"]}, lang, ctx)
        if text:
            parts.append(text)
    if inp.get("feedback"):    # regeneration after a failed check: drop optional embellishments
        parts = [p for p in parts if p]
    if not parts:
        parts = [render({"type": "handoff_notice", "reason": "system_failure"}, lang, ctx)]
    # merge into at most 3 messages: first directive stands alone, the rest are joined
    if len(parts) > 3:
        parts = [parts[0], parts[1], " ".join(parts[2:])]
    return {"parts": parts, "reaction_emoji": inp.get("reaction_emoji")}
