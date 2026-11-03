"""Authenticator-app codes (RFC 6238 over RFC 4226), standard library only: six digits, 30-second steps, SHA-1 (what every
authenticator app speaks). `verify` accepts the current step and one either side for clock drift, and refuses any step at or
before `after_step`, which is how a code is made single-use."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

STEP_S = 30
DIGITS = 6


def new_secret() -> str:
    """160 bits of randomness as base32, the form authenticator apps import."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    value = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return f"{value:0{DIGITS}d}"


def verify(secret: str, code: str, *, now: float, after_step: int = -1, window: int = 1) -> int | None:
    """The step the code belongs to, or None. Constant-time comparison; every step in the window is always checked."""
    if not (isinstance(code, str) and len(code) == DIGITS and code.isascii() and code.isdigit()):
        return None
    current = int(now // STEP_S)
    hit: int | None = None
    for step in range(current - window, current + window + 1):
        if hmac.compare_digest(code_at(secret, step), code) and step > after_step:
            hit = step
    return hit


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_S}"
