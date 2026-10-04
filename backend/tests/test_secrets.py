"""INV-11: owner access tokens are encrypted at rest; no secret appears in code, logs or fixtures."""
from __future__ import annotations

import base64
import re
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from salesai.config import Settings
from salesai.obs import redact

REPO = Path(__file__).resolve().parents[2]
BASE = {
    "DATABASE_URL": "postgresql://x", "PRICING_DATABASE_URL": "postgresql://x", "SYSTEM_DATABASE_URL": "postgresql://x",
    "MIGRATION_DATABASE_URL": "postgresql://x", "MASTER_KEY": base64.b64encode(b"k" * 32).decode(),
    "JWT_SECRET": "j" * 40, "OTP_SECRET": "o" * 40, "META_APP_SECRET": "m" * 24, "META_VERIFY_TOKEN": "v" * 24,
}


def settings(**over: str) -> Settings:
    return Settings(_env_file=None, **{k.lower(): v for k, v in {**BASE, **over}.items()})  # type: ignore[arg-type]


async def test_token_is_encrypted_with_a_per_tenant_key_bound_to_the_tenant(world, rt):
    a, b = await world.make_shop("Vault A"), await world.make_shop("Vault B")
    blob = await rt.vault.encrypt_token(a.business_id, "EAAG-secret-access-token")
    assert b"EAAG-secret-access-token" not in blob
    assert await rt.vault.decrypt_token(a.business_id, blob) == "EAAG-secret-access-token"
    with pytest.raises(Exception):  # noqa: B017,PT011  another tenant's key (and AAD) must not open it
        await rt.vault.decrypt_token(b.business_id, blob)


def test_logs_redact_tokens_keys_and_phone_numbers():
    line = "send failed for +919876543210 with Bearer EAAGabc123.def456 and key sk-ant-api03-abcdefghijklmnop"
    out = redact(line)
    assert "EAAGabc123" not in out and "sk-ant-api03" not in out and "9876543210" not in out


def test_production_refuses_the_simulator_the_local_llm_and_placeholder_secrets():
    for over in ({"ENV": "production", "SIMULATOR_ENABLED": "true", "OTP_CHANNEL": "whatsapp_cloud", "LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"},
                 {"ENV": "production", "SIMULATOR_ENABLED": "false", "OTP_CHANNEL": "whatsapp_cloud", "LLM_PROVIDER": "local"},
                 {"ENV": "production", "SIMULATOR_ENABLED": "false", "OTP_CHANNEL": "whatsapp_cloud", "LLM_PROVIDER": "anthropic",
                  "ANTHROPIC_API_KEY": "k", "JWT_SECRET": "dev-" + "x" * 40}):
        with pytest.raises(ValidationError):
            settings(**over)
    settings(ENV="production", SIMULATOR_ENABLED="false", OTP_CHANNEL="whatsapp_cloud", LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")


def test_missing_or_short_secrets_stop_startup():
    with pytest.raises(ValidationError):
        settings(JWT_SECRET="short")
    with pytest.raises(ValidationError):
        settings(MASTER_KEY=base64.b64encode(b"short").decode())


def test_no_credentials_are_committed_to_the_repository():
    try:
        files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()  # noqa: S603,S607
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    patterns = [re.compile(p) for p in (r"sk-ant-[A-Za-z0-9_\-]{24,}", r"EAA[A-Za-z0-9]{40,}", r"AKIA[0-9A-Z]{16}", r"-----BEGIN (RSA |EC )?PRIVATE KEY-----")]
    hits = []
    for f in files:
        p = REPO / f
        if p.suffix in {".png", ".jpg", ".woff2", ".lock", ".json"} and "openapi" not in f and "package-lock" in f:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        hits += [f"{f}: {pt.pattern}" for pt in patterns if pt.search(text) and not f.startswith("backend/tests/")]
    assert hits == []
