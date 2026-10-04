"""Phone number identity (E.164). The product is number-centric: owners log in by number and
customers are identified by their WhatsApp number (wa_id = digits, no '+')."""
from __future__ import annotations

import re

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def normalize_phone(raw: str, default_cc: str = "91") -> str:
    """'98765 43210' / '09876543210' / '+91 98765-43210' / '919876543210' -> '+919876543210'."""
    s = re.sub(r"[\s\-().]", "", raw.strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        digits = re.sub(r"\D", "", s)
        if digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 10:
            digits = default_cc + digits
        s = "+" + digits
    if not E164.match(s):
        raise ValueError("invalid phone number")
    return s


def wa_id(phone: str) -> str:
    return phone.lstrip("+")


def mask_phone(phone: str) -> str:
    d = phone.lstrip("+")
    return f"+{d[:2]}•••••{d[-3:]}" if len(d) > 6 else phone
