"""Envelope encryption for access tokens (INV-11) and small hashing helpers."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def new_data_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def wrap_key(master: bytes, dek: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(master).encrypt(nonce, dek, b"dek")


def unwrap_key(master: bytes, wrapped: bytes) -> bytes:
    return AESGCM(master).decrypt(wrapped[:12], wrapped[12:], b"dek")


def encrypt(dek: bytes, plaintext: str, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(dek).encrypt(nonce, plaintext.encode(), aad)


def decrypt(dek: bytes, blob: bytes, aad: bytes = b"") -> str:
    return AESGCM(dek).decrypt(blob[:12], blob[12:], aad).decode()


def hmac_hex(secret: str, *parts: str) -> str:
    return hmac.new(secret.encode(), "\x1f".join(parts).encode(), hashlib.sha256).hexdigest()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def numeric_code(digits: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
