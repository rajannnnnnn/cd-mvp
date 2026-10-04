"""Environment configuration, validated at startup. Invalid configuration stops the process."""
from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["api", "ingress", "web", "worker", "scheduler", "migrate", "all"]

ALL_QUEUES = (
    "inbound.events",
    "conversation.turns",
    "outbound.actions",
    "owner.notifications",
    "platform.events",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    env: Literal["dev", "staging", "production"] = "dev"
    role: Role = "all"
    log_level: str = "INFO"
    http_host: str = "0.0.0.0"  # noqa: S104
    http_port: int = 8000

    # --- database (four roles; see db/bootstrap_roles.sql)
    database_url: str = Field(..., description="app_user: tenant work, RLS applies")
    pricing_database_url: str = Field(..., description="app_pricing: pricing component only")
    system_database_url: str = Field(..., description="app_system: platform work, BYPASSRLS")
    migration_database_url: str = Field(..., description="app_owner: migrations only")
    db_pool_max: int = 10

    # --- queue / blob
    queue_backend: Literal["postgres", "redis"] = "postgres"
    redis_url: str = "redis://localhost:6379/0"
    worker_queues: str = ",".join(ALL_QUEUES)
    worker_concurrency: int = 8
    relay_poll_ms: int = 100
    blob_backend: Literal["fs"] = "fs"
    blob_path: str = "./.data/blobs"

    # --- secrets (environment or secret store only; INV-11)
    master_key: str = Field(..., description="base64 32 bytes; wraps per-tenant data keys")
    jwt_secret: str = Field(..., min_length=32)
    otp_secret: str = Field(..., min_length=32)
    meta_app_secret: str = Field(..., min_length=8)
    meta_verify_token: str = Field(..., min_length=8)
    meta_graph_version: str = "v21.0"
    meta_graph_base: str = "https://graph.facebook.com"

    # --- api
    allowed_origins: str = "http://localhost:5173,http://localhost:8080"
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 60 * 60 * 24 * 30
    otp_ttl_s: int = 300
    otp_max_attempts: int = 5
    otp_per_phone_per_hour: int = 5
    otp_per_ip_per_hour: int = 30

    # --- channels
    simulator_enabled: bool = True
    otp_channel: Literal["simulator", "whatsapp_cloud"] = "simulator"   # how login codes reach a number
    platform_wa_access_token: str = ""      # platform sender (login codes) when otp_channel=whatsapp_cloud
    # Business number the platform uses to send owner login codes / alerts when a business
    # has no number yet (e.g. operator onboarding).
    platform_sender_phone_number_id: str = "sim-platform"

    # --- LLM / transcription (provider is configuration; per tenant overrides possible)
    llm_provider: Literal["local", "anthropic"] = "local"
    llm_fallback_provider: Literal["local", "anthropic", "none"] = "none"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    llm_model_planner: str = "claude-sonnet-5-5"
    llm_model_writer: str = "claude-sonnet-5-5"
    llm_model_check: str = "claude-haiku-4-5"
    llm_timeout_s: float = 30.0

    # --- safety bounds
    max_turn_wait_ms_ceiling: int = 60_000
    default_owner_pause_minutes: int = 120

    @field_validator("master_key")
    @classmethod
    def _master_key_ok(cls, v: str) -> str:
        try:
            raw = base64.b64decode(v, validate=True)
        except Exception as e:  # noqa: BLE001
            raise ValueError("MASTER_KEY must be base64") from e
        if len(raw) != 32:
            raise ValueError("MASTER_KEY must decode to exactly 32 bytes")
        return v

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        if self.env == "production":
            if self.simulator_enabled or self.otp_channel == "simulator":
                raise ValueError("SIMULATOR_ENABLED must be false and OTP_CHANNEL=whatsapp_cloud in production")
            if self.llm_provider == "local":
                raise ValueError("LLM_PROVIDER=local is not allowed in production")
            weak = ("change-me", "dev-", "test-")
            for name in ("jwt_secret", "otp_secret", "meta_app_secret", "meta_verify_token"):
                if any(w in getattr(self, name).lower() for w in weak):
                    raise ValueError(f"{name.upper()} looks like a placeholder; set a real secret")
        unknown = set(self.queues) - set(ALL_QUEUES)
        if unknown:
            raise ValueError(f"unknown queues in WORKER_QUEUES: {sorted(unknown)}")
        return self

    @property
    def queues(self) -> list[str]:
        return [q.strip() for q in self.worker_queues.split(",") if q.strip()]

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def master_key_bytes(self) -> bytes:
        return base64.b64decode(self.master_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
