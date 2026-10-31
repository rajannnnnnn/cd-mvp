"""Channel gateway (ADR 0020 follow-up 2, INV-11): each process role is configured with the secrets it uses and no others.
`receive` (ingress) never holds the master key; `inbound` and the agent never hold it; only processes that can send hold it."""
from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from salesai.config import Settings
from salesai.modules.tenants.vault import TokenVault

DB = {"DATABASE_URL": "postgresql://x", "PRICING_DATABASE_URL": "postgresql://x", "SYSTEM_DATABASE_URL": "postgresql://x",
      "MIGRATION_DATABASE_URL": "postgresql://x"}
MASTER = base64.b64encode(b"k" * 32).decode()
SECRETS = {"MASTER_KEY": MASTER, "JWT_SECRET": "j" * 40, "OTP_SECRET": "o" * 40, "META_APP_SECRET": "m" * 24, "META_VERIFY_TOKEN": "v" * 24}


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """Settings reads os.environ; other test files export the whole secret set for the session. These tests are about what is ABSENT."""
    for name in list(SECRETS) + ["ROLE", "WORKER_QUEUES", "SIMULATOR_ENABLED", "ENV", "OTP_CHANNEL", "LLM_PROVIDER", "PAYMENT_PROVIDER"]:
        monkeypatch.delenv(name, raising=False)


def build(role: str, *, keep: tuple[str, ...] = (), **over: str) -> Settings:
    """Settings for `role` carrying only the named secrets (plus any override)."""
    env = {**DB, "ROLE": role, "SIMULATOR_ENABLED": "false", **{k: v for k, v in SECRETS.items() if k in keep}, **over}
    return Settings(_env_file=None, **{k.lower(): v for k, v in env.items()})  # type: ignore[arg-type]


def test_receive_needs_only_the_meta_secrets():
    s = build("ingress", keep=("META_APP_SECRET", "META_VERIFY_TOKEN"))
    assert s.master_key == "" and s.jwt_secret == "" and s.otp_secret == ""
    for missing in ("META_APP_SECRET", "META_VERIFY_TOKEN"):
        with pytest.raises(ValidationError, match=missing):
            build("ingress", keep=tuple(k for k in ("META_APP_SECRET", "META_VERIFY_TOKEN") if k != missing))


def test_the_inbound_and_agent_workers_hold_no_secrets_at_all():
    for queues in ("inbound.events", "conversation.turns", "inbound.events,conversation.turns"):
        build("worker", WORKER_QUEUES=queues)


def test_every_queue_that_can_send_needs_the_master_key():
    for queues in ("outbound.actions", "owner.notifications", "platform.events", "inbound.events,outbound.actions"):
        with pytest.raises(ValidationError, match="MASTER_KEY"):
            build("worker", WORKER_QUEUES=queues)
        build("worker", keep=("MASTER_KEY",), WORKER_QUEUES=queues)


def test_a_worker_with_the_default_queues_is_a_sender_and_needs_the_master_key():
    with pytest.raises(ValidationError, match="MASTER_KEY"):
        build("worker")
    build("worker", keep=("MASTER_KEY",))


def test_api_and_all_in_one_need_every_secret():
    for role in ("api", "all"):
        build(role, keep=tuple(SECRETS))
        for name in SECRETS:
            with pytest.raises(ValidationError, match=name):
                build(role, keep=tuple(k for k in SECRETS if k != name))


def test_scheduler_and_migrate_need_no_secrets_unless_the_simulator_signs_webhooks():
    build("scheduler")
    build("migrate")
    with pytest.raises(ValidationError, match="META_APP_SECRET"):
        build("scheduler", SIMULATOR_ENABLED="true")
    build("scheduler", keep=("META_APP_SECRET",), SIMULATOR_ENABLED="true")


def test_a_secret_that_is_present_must_still_be_strong_enough():
    with pytest.raises(ValidationError):
        build("ingress", keep=("META_APP_SECRET", "META_VERIFY_TOKEN"), META_APP_SECRET="short")
    with pytest.raises(ValidationError):
        build("worker", WORKER_QUEUES="outbound.actions", MASTER_KEY=base64.b64encode(b"short").decode())
    with pytest.raises(ValidationError, match="base64"):
        build("worker", WORKER_QUEUES="outbound.actions", MASTER_KEY="!!!not base64!!!")


def test_reading_a_missing_master_key_fails_loudly_instead_of_returning_nothing():
    s = build("ingress", keep=("META_APP_SECRET", "META_VERIFY_TOKEN"))
    with pytest.raises(RuntimeError, match="MASTER_KEY"):
        _ = s.master_key_bytes


async def test_a_vault_without_a_master_key_cannot_open_tokens():
    import uuid
    vault = TokenVault(None, None)  # type: ignore[arg-type]   # no database is reached: the missing key stops it first
    with pytest.raises(RuntimeError, match="MASTER_KEY"):
        await vault.decrypt_token(uuid.uuid4(), b"x")
    with pytest.raises(RuntimeError, match="MASTER_KEY"):
        await vault.encrypt_token(uuid.uuid4(), "token")
