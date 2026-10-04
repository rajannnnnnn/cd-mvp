"""Local deterministic extractor for owner configuration messages (stand-in for the `config` LLM stage)."""
from __future__ import annotations

import difflib
import re
from decimal import Decimal
from typing import Any

from salesai.modules.agent.llm.local_nlu import extract_numbers

PROFILE_KEYS = {
    "address": ("address", "pata", "पता", "location"), "hours": ("hours", "timing", "timings", "time", "samay", "समय", "open"),
    "delivery": ("delivery", "deliver", "shipping"), "payment_modes": ("payment", "payments", "pay", "upi"),
    "returns": ("return policy", "returns", "return", "exchange", "refund policy", "policy"), "phone": ("phone", "contact", "call", "number"),
}
FLOOR_WORDS = re.compile(r"\b(floor|minimum price|min price|lowest price|least price|cost price|bottom price|kam se kam)\b", re.I)


def _price_after(text: str) -> Decimal | None:
    nums = [n for n in extract_numbers(text) if n > 0]
    return nums[-1] if nums else None


def extract(inp: dict[str, Any]) -> dict[str, Any]:
    text = " ".join((inp.get("text") or "").split())
    low = text.lower()
    products: list[str] = inp.get("products") or []
    if not text:
        return {"ops": [], "clarification": "What would you like to change?"}
    if FLOOR_WORDS.search(low):
        return {"ops": [], "clarification": "For safety, minimum (floor) prices can only be set in the dashboard, not by chat."}

    # profile fields: "address: ...", "set hours Mon-Sat 10-8", "delivery - we deliver within 5 km"
    for field, keys in PROFILE_KEYS.items():
        for k in keys:
            m = re.match(rf"^(?:set |update |change |my |our )*{re.escape(k)}\s*(?:is|to|:|-|=)\s*(.+)$", text, re.I) \
                or re.match(rf"^(?:set |update |change )\s*{re.escape(k)}\s+(.+)$", text, re.I)
            if m and len(m.group(1)) > 2:
                return {"ops": [{"op": "set_profile", "field": field, "value": m.group(1).strip()}]}

    m = re.match(r"^(?:add |new |naya )?(?:fact|note|info)\s*(?::|-|=)\s*(.+)$", text, re.I)
    if m:
        return {"ops": [{"op": "add_fact", "value": m.group(1).strip()}]}

    m = re.match(r"^(?:add|new|naya|create)\s+(?:a |an )?(?:product|item|saman)\s+(.+)$", text, re.I)
    if m:
        rest = m.group(1)
        price = _price_after(rest)
        name = re.split(r"\s*(?:@|at|price|rate|for|₹|rs\.?|inr|-|:|,)\s*(?=\d|₹)", rest, maxsplit=1, flags=re.I)[0]
        name = re.sub(r"\s*\d[\d,]*(?:\.\d+)?\s*(?:rupees?|rs|/-|k)?\s*$", "", name, flags=re.I).strip(" -:,")
        if not name:
            return {"ops": [], "clarification": "What is the product's name?"}
        if price is None:
            return {"ops": [], "clarification": f"What is the price of {name}?"}
        return {"ops": [{"op": "add_product", "name": name[:200], "price": str(price)}]}

    # "price of red saree is 1400", "red saree ka price 1400 kar do", "set price red saree 1400"
    m = re.search(r"(?:price|rate|daam|kimat)\s*(?:of|for)?\s*(.+?)\s*(?:is|to|=|:|ka|ke|ki)?\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?\s*(?:k|hazar)?)", text, re.I) \
        or re.search(r"(.+?)\s+(?:ka|ke|ki)\s+(?:price|rate|daam|kimat)\s*(?:ab|now)?\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?\s*(?:k|hazar)?)", text, re.I)
    if m:
        ref, price = m.group(1).strip(" -:"), _price_after(m.group(2))
        ref = re.sub(r"^(?:set|change|update|make|the)\s+", "", ref, flags=re.I).strip()
        if ref and price is not None:
            match = difflib.get_close_matches(ref.lower(), [p.lower() for p in products], n=1, cutoff=0.5) or \
                [p.lower() for p in products if ref.lower() in p.lower() or p.lower() in ref.lower()]
            if not match:
                return {"ops": [], "clarification": f"I couldn't find a product called “{ref}”. Say “add product {ref} {price}” to create it."}
            real = next(p for p in products if p.lower() == match[0])
            return {"ops": [{"op": "set_price", "product": real, "price": str(price)}]}
    return {"ops": [], "clarification": "I didn't catch that. Try: “add product Red saree 1500”, “hours: Mon-Sat 10-8”, “address: …”, or send *help*."}
