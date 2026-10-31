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
# Queues whose jobs send to WhatsApp, so the process needs the master key to open the owner's access token.
SENDING_QUEUES = {"outbound.actions", "owner.notifications", "platform.events"}


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
    # Each secret is required only by the process roles that use it (ADR 0020, `_required_secrets`); a role that does not need one
    # is run without it, so a break-in there cannot read it. A secret that is present is always validated.
    master_key: str = Field("", description="base64 32 bytes; wraps per-tenant data keys. Senders and the API only")
    jwt_secret: str = Field("", description="at least 32 characters. API only")
    otp_secret: str = Field("", description="at least 32 characters. API only")
    meta_app_secret: str = Field("", description="at least 8 characters. Receive, API, and anything that signs simulated webhooks")
    meta_verify_token: str = Field("", description="at least 8 characters. Receive and API")
    meta_app_id: str = ""                  # Embedded Signup: the Meta app id (public) and its configuration id
    meta_config_id: str = ""
    meta_graph_version: str = "v21.0"
    meta_graph_base: str = "https://graph.facebook.com"

    # --- api
    allowed_origins: str = "http://localhost:5173,http://localhost:8080"
    trust_proxy: bool = True             # honour X-Forwarded-For from the reverse proxy
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 60 * 60 * 24 * 30
    otp_ttl_s: int = 300
    otp_max_attempts: int = 5
    otp_accept_any: bool = False          # DEMO ONLY: any code signs a number in (refused in production, see _production_rules)
    otp_min_interval_s: int = 20          # spacing between code requests for one number
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

    # --- billing: how money is collected. `test` settles instantly (demo); `manual` = bank transfer/UPI marked paid by the platform team
    payment_provider: Literal["test", "manual"] = "test"
    payment_instructions: str = "Pay by UPI or bank transfer to the account shared by our team and quote the invoice number."
    invoice_seller_name: str = "[Company name]"
    invoice_seller_address: str = "[Registered address]"
    invoice_seller_gstin: str = "[GSTIN]"

    # --- alert routing: where operator alerts are pushed (empty = console only)
    alert_webhook_url: str = ""
    alert_whatsapp_numbers: str = ""        # comma-separated E.164 numbers of the platform team
    alert_min_severity: Literal["info", "warning", "critical"] = "warning"
    alert_reminder_minutes: int = 60        # unresolved critical alerts repeat at this interval

    # --- safety bounds
    max_turn_wait_ms_ceiling: int = 60_000
    default_owner_pause_minutes: int = 120
    max_ai_turns_per_conversation_10min: int = 30   # flood guard: beyond this the AI stops replying in that chat for a while

    # --- channel gateway raw webhook log (ADR 0020)
    raw_webhook_retention_days: int = Field(30, ge=1)          # verified requests
    invalid_webhook_retention_days: int = Field(3, ge=1)       # requests that failed signature verification
    invalid_webhook_max_bytes: int = Field(4096, ge=0)         # how much of a failed request's body is kept

    @field_validator("master_key")
    @classmethod
    def _master_key_ok(cls, v: str) -> str:
        if not v:
            return v                                   # absence is judged per role in _required_secrets
        try:
            raw = base64.b64decode(v, validate=True)
        except Exception as e:  # noqa: BLE001
            raise ValueError("MASTER_KEY must be base64") from e
        if len(raw) != 32:
            raise ValueError("MASTER_KEY must decode to exactly 32 bytes")
        return v

    @model_validator(mode="after")
    def _required_secrets(self) -> Settings:
        """INV-11 / ADR 0020: each role gets the secrets it uses and no more; a required one that is missing stops startup."""
        need: set[str] = set()
        if self.role in ("api", "all"):
            need |= {"master_key", "jwt_secret", "otp_secret", "meta_app_secret", "meta_verify_token"}
        elif self.role == "ingress":
            need |= {"meta_app_secret", "meta_verify_token"}
        elif self.role == "worker" and set(self.queues) & SENDING_QUEUES:
            need.add("master_key")                     # decrypts the owner's WhatsApp token to send
        if self.simulator_enabled and self.role not in ("web", "migrate"):
            need.add("meta_app_secret")                # the simulator signs the webhooks it plays back
        for name in sorted(need):
            if not getattr(self, name):
                raise ValueError(f"{name.upper()} is required for role {self.role!r}")
        for name, minimum in (("jwt_secret", 32), ("otp_secret", 32), ("meta_app_secret", 8), ("meta_verify_token", 8)):
            v = getattr(self, name)
            if v and len(v) < minimum:
                raise ValueError(f"{name.upper()} must be at least {minimum} characters")
        return self

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        if self.env == "production":
            if self.otp_accept_any:
                raise ValueError("OTP_ACCEPT_ANY must be false in production: it would let anyone sign in as any number")
            if self.simulator_enabled or self.otp_channel == "simulator":
                raise ValueError("SIMULATOR_ENABLED must be false and OTP_CHANNEL=whatsapp_cloud in production")
            if self.payment_provider == "test":
                raise ValueError("PAYMENT_PROVIDER=test settles invoices without taking money; use PAYMENT_PROVIDER=manual in production")
            if self.llm_provider == "local":
                raise ValueError("LLM_PROVIDER=local is not allowed in production")
            weak = ("change-me", "dev-", "test-")
            for name in ("jwt_secret", "otp_secret", "meta_app_secret", "meta_verify_token"):
                if any(w in getattr(self, name).lower() for w in weak):
                    raise ValueError(f"{name.upper()} looks like a placeholder; set a real secret")
        for n in self.alert_numbers:
            if not n.startswith("+") or not n[1:].isdigit():
                raise ValueError(f"ALERT_WHATSAPP_NUMBERS entry {n!r} must look like +919999900000")
        unknown = set(self.queues) - set(ALL_QUEUES)
        if unknown:
            raise ValueError(f"unknown queues in WORKER_QUEUES: {sorted(unknown)}")
        return self

    @property
    def queues(self) -> list[str]:
        return [q.strip() for q in self.worker_queues.split(",") if q.strip()]

    @property
    def alert_numbers(self) -> list[str]:
        return [n.strip() for n in self.alert_whatsapp_numbers.split(",") if n.strip()]

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def master_key_bytes(self) -> bytes:
        if not self.master_key:
            raise RuntimeError(f"MASTER_KEY is not configured for role {self.role!r}")
        return base64.b64decode(self.master_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
