"""Authenticator codes (RFC 6238), standard library only. The vectors are the RFC's own."""
from __future__ import annotations

import base64

from salesai.modules.auth import totp

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()      # the RFC's SHA-1 test key


def test_codes_match_the_rfc_6238_vectors():
    # RFC 6238 appendix B, SHA-1, last six digits of the 8-digit values
    for t, expect in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037")):
        assert totp.code_at(RFC_SECRET, t // 30) == expect


def test_a_code_is_accepted_for_its_own_step_and_one_either_side_but_no_further():
    step = 1_000_000
    code = totp.code_at(RFC_SECRET, step)
    assert totp.verify(RFC_SECRET, code, now=step * 30 + 5) == step
    assert totp.verify(RFC_SECRET, code, now=(step + 1) * 30 + 5) == step           # a code typed a little late
    assert totp.verify(RFC_SECRET, code, now=(step - 1) * 30 + 5) == step           # a phone clock a little fast
    assert totp.verify(RFC_SECRET, code, now=(step + 3) * 30) is None
    assert totp.verify(RFC_SECRET, "000000", now=step * 30) is None


def test_a_step_already_used_is_refused():
    step = 1_000_000
    code = totp.code_at(RFC_SECRET, step)
    assert totp.verify(RFC_SECRET, code, now=step * 30, after_step=step - 1) == step
    assert totp.verify(RFC_SECRET, code, now=step * 30, after_step=step) is None     # replay


def test_malformed_input_is_just_wrong():
    for bad in ("", "12345", "1234567", "12 456", "abcdef", "١٢٣٤٥٦"):
        assert totp.verify(RFC_SECRET, bad, now=0) is None


def test_a_new_secret_is_long_random_base32_and_the_uri_names_the_issuer():
    a, b = totp.new_secret(), totp.new_secret()
    assert a != b and len(a) >= 32 and base64.b32decode(a)
    uri = totp.provisioning_uri(a, "founder@chatdeal", "ChatDeal Admin")
    assert uri.startswith("otpauth://totp/") and f"secret={a}" in uri and "issuer=ChatDeal%20Admin" in uri
