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
ADMIN_SECRET = "a" * 40            # the console's own secret; held only by the admin part, so it is not in SECRETS (those are the shop side's)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """Settings reads os.environ; other test files export the whole secret set for the session. These tests are about what is ABSENT."""
    for name in list(SECRETS) + ["ADMIN_JWT_SECRET", "ROLE", "WORKER_QUEUES", "SIMULATOR_ENABLED", "ENV", "OTP_CHANNEL", "LLM_PROVIDER", "PAYMENT_PROVIDER"]:
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
    # ADR 0025: the agent decides and asks, the gateway sends, so none of the agent's queues needs the master key
    agent = "conversation.events,conversation.turns,owner.notifications,platform.events"
    for queues in ("inbound.events", "conversation.events", "conversation.turns", "owner.notifications", "platform.events", agent,
                   f"inbound.events,{agent}"):
        build("worker", WORKER_QUEUES=queues)


def test_only_the_gateways_send_queue_needs_the_master_key_and_the_meta_app_secret():
    # it decrypts the shop's WhatsApp token to send, and completes Embedded Signup with Meta (ADR 0025)
    for queues in ("outbound.actions", "inbound.events,outbound.actions", "conversation.turns,outbound.actions"):
        for missing in ("MASTER_KEY", "META_APP_SECRET"):
            with pytest.raises(ValidationError, match=missing):
                build("worker", keep=tuple(k for k in ("MASTER_KEY", "META_APP_SECRET") if k != missing), WORKER_QUEUES=queues)
        build("worker", keep=("MASTER_KEY", "META_APP_SECRET"), WORKER_QUEUES=queues)


def test_the_sender_needs_the_platform_token_when_codes_go_out_over_whatsapp():
    keep = ("MASTER_KEY", "META_APP_SECRET")
    build("worker", keep=keep, WORKER_QUEUES="outbound.actions")                                    # simulator channel: no token needed
    with pytest.raises(ValidationError, match="PLATFORM_WA_ACCESS_TOKEN"):
        build("worker", keep=keep, WORKER_QUEUES="outbound.actions", OTP_CHANNEL="whatsapp_cloud")
    build("worker", keep=keep, WORKER_QUEUES="outbound.actions", OTP_CHANNEL="whatsapp_cloud", PLATFORM_WA_ACCESS_TOKEN="EAAtoken")


def test_the_web_api_holds_no_meta_secret_at_all():
    web = ("JWT_SECRET", "OTP_SECRET")                          # ADR 0028: not the master key either; the admin part holds it for pilot numbers
    s = build("api", keep=web)
    assert s.meta_app_secret == "" and s.meta_verify_token == "" and s.master_key == "" and s.admin_jwt_secret == ""
    for name in web:
        with pytest.raises(ValidationError, match=name):
            build("api", keep=tuple(k for k in web if k != name))


def test_the_admin_part_holds_the_master_key_and_its_own_secret_and_no_meta_secret():
    keep = ("MASTER_KEY", "JWT_SECRET", "OTP_SECRET")
    s = build("admin", keep=keep, ADMIN_JWT_SECRET=ADMIN_SECRET)
    assert s.meta_app_secret == "" and s.meta_verify_token == ""
    for name in (*keep, "ADMIN_JWT_SECRET"):
        with pytest.raises(ValidationError, match=name):
            build("admin", keep=tuple(k for k in keep if k != name), ADMIN_JWT_SECRET="" if name == "ADMIN_JWT_SECRET" else ADMIN_SECRET)


def test_a_worker_with_the_default_queues_is_a_sender_and_needs_the_gateways_secrets():
    with pytest.raises(ValidationError, match="MASTER_KEY"):
        build("worker")
    build("worker", keep=("MASTER_KEY", "META_APP_SECRET"))


def test_all_in_one_needs_every_secret():
    build("all", keep=tuple(SECRETS))
    for name in SECRETS:
        with pytest.raises(ValidationError, match=name):
            build("all", keep=tuple(k for k in SECRETS if k != name))


PROD = {"ENV": "production", "SIMULATOR_ENABLED": "false", "OTP_CHANNEL": "whatsapp_cloud", "PAYMENT_PROVIDER": "manual",
        "PLATFORM_WA_ACCESS_TOKEN": "EAAtoken"}


def test_only_the_agents_workers_need_a_model_configured_in_production():
    # the web and gateway parts never call a model, so asking them for an API key (or refusing the stand-in) is noise
    build("api", keep=("JWT_SECRET", "OTP_SECRET"), **PROD)
    build("admin", keep=("MASTER_KEY", "JWT_SECRET", "OTP_SECRET"), ADMIN_JWT_SECRET=ADMIN_SECRET, ADMIN_ALLOWED_ORIGINS="https://admin.example.com", **PROD)
    build("ingress", keep=("META_APP_SECRET", "META_VERIFY_TOKEN"), **PROD)
    build("scheduler", **PROD)
    build("worker", keep=("MASTER_KEY", "META_APP_SECRET"), WORKER_QUEUES="outbound.actions,inbound.events", **PROD)
    for queues in ("conversation.turns", "owner.notifications", "conversation.events,conversation.turns,owner.notifications"):
        with pytest.raises(ValidationError, match="local"):
            build("worker", WORKER_QUEUES=queues, **PROD)                                          # the agent does call one
        build("worker", WORKER_QUEUES=queues, LLM_PROVIDER="openai_compat", OPENAI_COMPAT_BASE_URL="https://llm.example/v1",
              OPENAI_COMPAT_API_KEY="key", **PROD)
    with pytest.raises(ValidationError, match="local"):
        build("all", keep=tuple(SECRETS), **PROD)


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


# ------------------------------------------------------------------ the floor-reading database login (INV-1, ADR 0025, ADR 0042)
NO_PRICING = {"PRICING_DATABASE_URL": ""}


def test_only_the_agents_turn_worker_holds_the_login_that_can_read_floor_prices():
    # the pricing role is the only one that can read price_floors; the only code that uses it is the agent deciding a turn
    for queues in ("conversation.turns", "conversation.events,conversation.turns,owner.notifications"):
        with pytest.raises(ValidationError, match="PRICING_DATABASE_URL"):
            build("worker", WORKER_QUEUES=queues, **NO_PRICING)
        build("worker", WORKER_QUEUES=queues)
    with pytest.raises(ValidationError, match="PRICING_DATABASE_URL"):
        build("all", keep=tuple(SECRETS), **NO_PRICING)
    # everything else runs without it, so a breach of the web part, the webhook receiver, the gateway, the scheduler or the console cannot read a floor
    build("api", keep=("JWT_SECRET", "OTP_SECRET"), **NO_PRICING)
    build("admin", keep=("MASTER_KEY", "JWT_SECRET", "OTP_SECRET"), ADMIN_JWT_SECRET=ADMIN_SECRET, **NO_PRICING)
    build("ingress", keep=("META_APP_SECRET", "META_VERIFY_TOKEN"), **NO_PRICING)
    build("scheduler", **NO_PRICING)
    build("migrate", **NO_PRICING)
    build("worker", keep=("MASTER_KEY", "META_APP_SECRET"), WORKER_QUEUES="outbound.actions", **NO_PRICING)
    build("worker", WORKER_QUEUES="inbound.events,owner.notifications,platform.events", **NO_PRICING)


def test_a_part_without_the_login_cannot_reach_floors_even_by_mistake():
    from salesai.db import Database
    db = Database("postgresql://u@localhost/x", None, "postgresql://s@localhost/x")
    assert db.pricing is None
    with pytest.raises(RuntimeError, match="no pricing database login"):
        import asyncio
        async def go():
            async with db.pricing_tenant("00000000-0000-0000-0000-000000000000"):
                pass
        asyncio.run(go())
