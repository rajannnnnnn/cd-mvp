"""Environment configuration, validated at startup. Invalid configuration stops the process."""
from __future__ import annotations

import base64
import ipaddress
import json
import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["api", "ingress", "web", "admin", "worker", "scheduler", "migrate", "all"]

ALL_QUEUES = (
    "inbound.events",
    "conversation.events",
    "conversation.turns",
    "outbound.actions",
    "owner.notifications",
    "platform.events",
)
# Queues whose workers call a model: the agent's turns, and the owner loop's config-by-chat (ADR 0025, ADR 0034).
MODEL_QUEUES = {"conversation.turns", "owner.notifications"}
# Queues whose jobs send to WhatsApp, so the process needs the master key to open the owner's access token.
# Only the gateway's send queue does (ADR 0025): the owner loop and the platform events no longer send, they ask the gateway.
SENDING_QUEUES = {"outbound.actions"}
# The queue whose jobs decide a turn, which is the one place a price floor is read (INV-1): only its worker holds the floor-reading database login.
TURN_QUEUES = {"conversation.turns"}


# The planner's intents (modules/agent/models.py `Intent`; a test keeps this list in step) and the ones the rules may decide alone.
KNOWN_INTENTS = ("greeting", "product_inquiry", "price_query", "discount_request", "counter_offer", "availability", "business_info", "order_intent",
                 "visit_intent", "affirmation", "complaint", "human_request", "out_of_scope", "unsupported_media", "not_interested", "identity_question",
                 "thanks", "goodbye", "smalltalk", "unknown", "nudge")
DEFAULT_TIERING_INTENTS = ("greeting", "thanks", "goodbye", "identity_question", "human_request", "complaint", "not_interested", "unsupported_media",
                           "business_info", "price_query", "availability", "product_inquiry")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    env: Literal["dev", "staging", "production"] = "dev"
    role: Role = "all"
    log_level: str = "INFO"
    http_host: str = "0.0.0.0"  # noqa: S104
    http_port: int = 8000

    # --- database (four roles; see db/bootstrap_roles.sql)
    database_url: str = Field(..., description="app_user: tenant work, RLS applies")
    pricing_database_url: str = Field("", description="app_pricing: the only login that can read price floors; held by the agent's turn worker and nobody else (INV-1)")
    system_database_url: str = Field(..., description="app_system: platform work, BYPASSRLS")
    console_database_url: str = Field("", description="app_console: the founder console only; the one login that can read admin sign-in secrets and sessions (ADR 0043)")
    migration_database_url: str = Field(..., description="app_owner: migrations only")
    db_pool_max: int = 10

    # --- queue / blob
    queue_backend: Literal["postgres", "redis"] = "postgres"
    redis_url: str = "redis://localhost:6379/0"
    worker_queues: str = ",".join(ALL_QUEUES)
    scheduler_component: Literal["all", "web", "gateway", "agent"] = "all"   # which component's timers this scheduler runs (ADR 0025)
    worker_concurrency: int = 8
    # restart tolerance (ADR 0035): a job's lease is short and renewed while it runs, so a crashed worker's jobs return within
    # seconds; on a stop request, unfinished jobs are given back after the grace period instead of waiting for a lease
    worker_lease_s: int = Field(30, ge=5, le=600)
    worker_shutdown_grace_s: float = Field(20.0, ge=0, le=300)
    # the recovery sweep: work the database says should be happening that nobody is doing
    recovery_stuck_after_s: float = Field(90.0, ge=0)         # an unanswered customer message this old with nobody working on it is re-driven
    recovery_retry_after_s: float = Field(180.0, ge=0)        # ...and given this long to work before it is tried again
    recovery_max_attempts: int = Field(3, ge=1)               # after this many tries the owner is handed the chat and the founder is alerted
    recovery_queued_reply_s: float = Field(120.0, ge=0)       # a reply planned this long ago and still not sent is requested again
    recovery_mailbox_s: float = Field(60.0, ge=0)             # a hand-off row between components nobody picked up for this long is requested again
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
    meta_sdk_url: str = "https://connect.facebook.net/en_US/sdk.js"      # the browser script that opens Meta's sign-up popup; an address like META_GRAPH_BASE
    meta_signup_origins: str = "https://www.facebook.com,https://web.facebook.com"   # origins the sign-up popup's messages are accepted from
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
    otp_delivery_wait_s: float = Field(6.0, gt=0, le=30)   # how long a sign-in request waits for the gateway to send the code
    number_connect_wait_s: float = Field(30.0, gt=0, le=120)   # how long a connect request waits for the gateway (several Meta calls)
    otp_per_phone_per_hour: int = 5
    otp_per_ip_per_hour: int = 30

    # --- the admin plane (ADR 0028): the founder's console is its own application on its own origin
    admin_jwt_secret: str = Field("", description="at least 32 characters, different from JWT_SECRET. Admin part only")
    admin_session_minutes: int = 30           # how long a console session lasts; there is no refresh, only a fresh authenticator code
    admin_allowed_origins: str = ""           # the console's own origin(s), e.g. https://admin.yourcompany.example
    admin_allowed_ips: str = ""               # optional: comma-separated addresses or ranges that may reach the console (empty = anyone who can sign in)

    # --- channels
    simulator_enabled: bool = False   # the product's own in-process stand-in network: development and tests only, never by default (ADR 0045)
    otp_channel: Literal["simulator", "whatsapp_cloud"] = "whatsapp_cloud"   # how login codes reach a number
    platform_wa_access_token: str = ""      # platform sender (login codes) when otp_channel=whatsapp_cloud
    # Business number the platform uses to send owner login codes / alerts when a business
    # has no number yet (e.g. operator onboarding).
    platform_sender_phone_number_id: str = "sim-platform"

    # --- LLM (ADR 0034). Which vendor and model run each pipeline stage is configuration: switching a model, a host or
    # a vendor is a settings change and a restart, never a code change. Founder's default: openai/gpt-oss-20b on every
    # stage at medium effort, until the benchmark says a stage needs more. Misconfiguration stops startup.
    # Rules before models (ADR 0039): the planner tries deterministic rules first and keeps their answer only for the cases below, when they are
    # sure and it is safe; everything else goes to the language model. Money, negotiation and commitments are never on the list.
    tiering_enabled: bool = True
    tiering_planner_intents: str = ""          # comma-separated intents the rules may decide alone; empty = the built-in safe list
    tiering_skip_check_when_low_risk: bool = True    # skip the optional model check for replies that state nothing risky (greetings, thanks, questions back)
    llm_provider: Literal["local", "anthropic", "openai_compat"] = "local"
    llm_provider_planner: Literal["", "local", "anthropic", "openai_compat"] = ""   # "" = llm_provider
    llm_provider_writer: Literal["", "local", "anthropic", "openai_compat"] = ""
    llm_provider_check: Literal["", "local", "anthropic", "openai_compat"] = ""
    llm_fallback_provider: Literal["local", "anthropic", "openai_compat", "none"] = "none"
    llm_fallback_model: str = ""                       # model the fallback runs; "" = each stage's own model
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_compat_base_url: str = ""                   # e.g. https://api.groq.com/openai/v1, or a self-hosted vLLM
    openai_compat_api_key: str = ""
    openai_compat_thinking_tokens: int = Field(0, ge=0, le=32000)   # extra tokens per reply for a thinking model (Gemini 2.5, gpt-oss); billed only as used
    llm_json_mode: Literal["json_schema", "json_object"] = "json_schema"   # json_object for hosts without schema support
    llm_model_planner: str = "openai/gpt-oss-20b"
    llm_model_writer: str = "openai/gpt-oss-20b"
    llm_model_check: str = "openai/gpt-oss-20b"
    llm_effort_planner: Literal["low", "medium", "high"] = "medium"
    llm_effort_writer: Literal["low", "medium", "high"] = "medium"
    llm_effort_check: Literal["low", "medium", "high"] = "low"
    llm_prices: str = ""                               # JSON {"model": [input, output, cached_input, cache_write]}, USD/1M tokens
    llm_timeout_s: float = 30.0

    def llm_stage_provider(self, stage: str) -> str:
        return str(getattr(self, f"llm_provider_{stage}", "") or self.llm_provider)

    # --- Meta message pricing (ADR 0026). Estimates only: Meta changed these twice in 2026; re-check Meta's own pricing
    # page. Rates in INR per delivered message by category; the allowance is free service messages per number per month.
    meta_rates_inr: str = '{"service": 0.115, "utility": 0.115, "authentication": 0.115, "marketing": 0.8631}'
    meta_free_service_per_number: int = Field(default=1000, ge=0)

    @property
    def meta_rates(self) -> dict[str, float]:
        return {k: float(v) for k, v in json.loads(self.meta_rates_inr).items()}

    # --- billing: how money is collected. `test` settles instantly (demo); `manual` = bank transfer/UPI marked paid by the platform team
    payment_provider: Literal["test", "manual"] = "test"
    payment_instructions: str = "Pay by UPI or bank transfer to the account shared by our team and quote the invoice number."
    invoice_seller_name: str = "[Company name]"
    invoice_seller_address: str = "[Registered address]"
    invoice_seller_gstin: str = "[GSTIN]"

    # --- alert routing (ADR 0029): where platform alerts go. Empty = the operator console only. They go to the platform team
    # (the founder) and never to a shop owner: the recipients below are the only addresses the alert path knows.
    deletion_grace_days: int = Field(60, ge=0, le=365, description="days between an owner asking to delete their shop and the erasure; the owner can cancel until then")
    alert_email_to: str = ""                # comma-separated addresses, e.g. you@yourcompany.example
    alert_email_from: str = ""              # the sender address the mail provider allows, e.g. alerts@yourcompany.example
    smtp_host: str = ""
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = Field("", description="the mail provider's SMTP password or API key. Web part only")
    alert_webhook_url: str = ""
    alert_whatsapp_numbers: str = ""        # comma-separated E.164 numbers of the platform team (Critical only)
    alert_min_severity: Literal["low", "medium", "high", "critical"] = "low"
    alert_reminder_minutes: int = Field(30, ge=1)           # an unresolved Critical alert repeats at this interval
    alert_high_cooldown_minutes: int = Field(30, ge=1)      # a High alert is repeated at most this often, and only if it happened again
    alert_medium_rollup_minutes: int = Field(60, ge=1)      # Medium alerts are gathered into one email per this interval
    alert_low_digest_minutes: int = Field(1440, ge=1)       # Low alerts are gathered into one digest per this interval

    @field_validator("alert_min_severity", mode="before")
    @classmethod
    def _old_severity_names(cls, v: Any) -> Any:
        """Existing env files say info / warning; they mean low / medium."""
        return {"info": "low", "warning": "medium"}.get(v, v)

    # --- safety bounds
    max_turn_wait_ms_ceiling: int = 60_000
    default_owner_pause_minutes: int = 120
    max_ai_turns_per_conversation_10min: int = 30   # flood guard: beyond this the AI stops replying in that chat for a while

    # --- channel gateway raw webhook log (ADR 0020)
    raw_webhook_retention_days: int = Field(30, ge=1)          # verified requests
    invalid_webhook_retention_days: int = Field(3, ge=1)       # requests that failed signature verification
    # ADR 0030. Chat history is kept while the shop is active unless a period is set: how long to keep customers'
    # conversations is a privacy/legal policy for the founder (go-live checklist). 0 = keep while the shop is active.
    chat_retention_days: int = Field(0, ge=0)                  # messages, owner alerts, learning records
    turn_retention_days: int = Field(0, ge=0)                  # AI decision records (cost and audit data)
    sim_message_retention_days: int = Field(30, ge=1)          # simulator chats: demo data only
    resolved_alert_retention_days: int = Field(180, ge=1)      # operator alerts after they are resolved
    resolved_ticket_attachment_retention_days: int = Field(180, ge=1)   # pictures on support tickets resolved this long ago
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
        if self.role == "all":
            need |= {"master_key", "jwt_secret", "otp_secret", "meta_app_secret", "meta_verify_token"}
        elif self.role == "api":                       # the public web part holds no Meta secret and no master key (ADR 0025, 0028)
            need |= {"jwt_secret", "otp_secret"}
        elif self.role == "admin":                     # the founder's console: the master key is for registering a pilot number by hand
            need |= {"master_key", "jwt_secret", "otp_secret", "admin_jwt_secret"}
        elif self.role == "ingress":
            need |= {"meta_app_secret", "meta_verify_token"}
        elif self.role == "worker" and set(self.queues) & SENDING_QUEUES:
            need |= {"master_key", "meta_app_secret"}  # the gateway's send part: opens the WhatsApp tokens, completes Embedded Signup
            if self.otp_channel == "whatsapp_cloud":
                need.add("platform_wa_access_token")   # the platform's own sender: sign-in codes, and alerts when a shop's number is down
        if self.simulator_enabled and self.role not in ("web", "admin", "migrate"):
            need.add("meta_app_secret")                # the simulator signs the webhooks it plays back
        if self.role in ("admin", "all"):
            need.add("console_database_url")           # ADR 0043: the console's own database login
        if self.reads_floors:
            need.add("pricing_database_url")           # INV-1: the login that can read price_floors goes to the agent's turn worker only
        missing = [n.upper() for n in sorted(need) if not getattr(self, n)]
        if missing:                          # all of them at once, so one start-up report shows everything that is missing
            raise ValueError(f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required for role {self.role!r}")
        for name, minimum in (("jwt_secret", 32), ("otp_secret", 32), ("admin_jwt_secret", 32), ("meta_app_secret", 8), ("meta_verify_token", 8)):
            v = getattr(self, name)
            if v and len(v) < minimum:
                raise ValueError(f"{name.upper()} must be at least {minimum} characters")
        if self.admin_jwt_secret and self.admin_jwt_secret == self.jwt_secret:
            raise ValueError("ADMIN_JWT_SECRET must differ from JWT_SECRET: the public application must not be able to sign admin sessions")
        if not 5 <= self.admin_session_minutes <= 480:
            raise ValueError("ADMIN_SESSION_MINUTES must be between 5 and 480")
        for cidr in self.admin_ips:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as e:
                raise ValueError(f"ADMIN_ALLOWED_IPS entry {cidr!r} is not an address or range") from e
        return self

    @property
    def reads_floors(self) -> bool:
        """Whether this process decides turns and so reads price floors: the agent's turn worker, or everything-in-one."""
        return self.role == "all" or (self.role == "worker" and bool(set(self.queues) & TURN_QUEUES))

    @property
    def uses_model(self) -> bool:
        """Whether this process can call a model: the agent's workers, or everything-in-one. The web and gateway parts never do."""
        return self.role == "all" or (self.role == "worker" and bool(set(self.queues) & MODEL_QUEUES))

    @model_validator(mode="before")
    @classmethod
    def _a_comment_is_not_a_value(cls, data: Any) -> Any:
        """`KEY=   # note` in an env file reaches the process as the text of the note on some readers (docker compose does). It is an empty setting, so
        the operator is told it is missing instead of the product starting on a made-up secret or address list."""
        if isinstance(data, dict):
            return {k: ("" if isinstance(v, str) and v.lstrip().startswith("#") else v) for k, v in data.items()}
        return data

    @model_validator(mode="after")
    def _llm_rules(self) -> Settings:
        """ADR 0034: a vendor any stage (or the fallback) uses must be reachable, and a model must suit its vendor.
        Prices are checked by the agent's factory at startup; here only their shape, since config imports no module.
        Only processes that can call a model are held to this: asking the web or gateway part for an API key is noise."""
        if not self.uses_model:
            return self
        stages = {st: self.llm_stage_provider(st) for st in ("planner", "writer", "check")}
        used = set(stages.values()) | ({self.llm_fallback_provider} - {"none"})
        if "anthropic" in used and not self.anthropic_api_key:
            raise ValueError("an LLM stage uses anthropic, which requires ANTHROPIC_API_KEY")
        if "openai_compat" in used and not (self.openai_compat_base_url and self.openai_compat_api_key):
            raise ValueError("an LLM stage uses openai_compat, which requires OPENAI_COMPAT_BASE_URL and OPENAI_COMPAT_API_KEY")
        for st, prov in stages.items():
            model = getattr(self, f"llm_model_{st}")
            if prov == "anthropic" and not model.startswith("claude-"):
                raise ValueError(f"LLM_MODEL_{st.upper()}={model!r} runs on anthropic, which serves only claude- models")
        if self.llm_fallback_provider == "anthropic" and self.llm_fallback_model and not self.llm_fallback_model.startswith("claude-"):
            raise ValueError(f"LLM_FALLBACK_MODEL={self.llm_fallback_model!r} runs on anthropic, which serves only claude- models")
        if self.llm_prices.strip():
            try:
                rows = json.loads(self.llm_prices)
            except json.JSONDecodeError as e:
                raise ValueError(f"LLM_PRICES is not valid JSON: {e}") from e
            if not isinstance(rows, dict) or not all(
                    isinstance(r, list) and len(r) == 4 and all(isinstance(x, int | float) and x >= 0 for x in r)
                    for r in rows.values()):
                raise ValueError("LLM_PRICES must map each model to four non-negative numbers: "
                                 "[input, output, cached_input, cache_write] in USD per 1M tokens")
        try:
            rates = json.loads(self.meta_rates_inr)
        except json.JSONDecodeError as e:
            raise ValueError(f"META_RATES_INR is not valid JSON: {e}") from e
        if not (isinstance(rates, dict) and {"service", "utility"} <= set(rates)
                and all(isinstance(v, int | float) and v >= 0 for v in rates.values())):
            raise ValueError("META_RATES_INR must map categories to non-negative INR rates and include service and utility")
        if self.env == "production" and "local" in used:
            raise ValueError("the local LLM stand-in is not allowed in production on any stage or as the fallback")
        return self

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.env == "production":
            if self.otp_accept_any:
                raise ValueError("OTP_ACCEPT_ANY must be false in production: it would let anyone sign in as any number")
            if self.simulator_enabled or self.otp_channel == "simulator":
                raise ValueError("SIMULATOR_ENABLED must be false and OTP_CHANNEL=whatsapp_cloud in production")
            if self.payment_provider == "test":
                raise ValueError("PAYMENT_PROVIDER=test settles invoices without taking money; use PAYMENT_PROVIDER=manual in production")
            if self.role == "admin" and (not self.admin_origins or "*" in self.admin_origins):
                raise ValueError("ADMIN_ALLOWED_ORIGINS must name the console's own origin in production (never empty or *)")
            weak = ("change-me", "dev-", "test-")
            for name in ("jwt_secret", "otp_secret", "admin_jwt_secret", "meta_app_secret", "meta_verify_token"):
                if any(w in getattr(self, name).lower() for w in weak):
                    raise ValueError(f"{name.upper()} looks like a placeholder; set a real secret")
        unknown_intents = sorted(set(n.strip() for n in self.tiering_planner_intents.split(",") if n.strip()) - set(KNOWN_INTENTS))
        if unknown_intents:
            raise ValueError(f"TIERING_PLANNER_INTENTS names intents that do not exist: {unknown_intents}")
        for e in self.alert_emails:
            if not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+", e):
                raise ValueError(f"ALERT_EMAIL_TO entry {e!r} is not an email address")
        if self.alert_emails:
            if not self.smtp_host:
                raise ValueError("SMTP_HOST is required when ALERT_EMAIL_TO is set")
            if not self.alert_email_from:
                raise ValueError("ALERT_EMAIL_FROM is required when ALERT_EMAIL_TO is set")
        if self.smtp_username and not self.smtp_password:
            raise ValueError("SMTP_PASSWORD is required when SMTP_USERNAME is set")
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
    def tiering_intents(self) -> frozenset[str]:
        names = [n.strip() for n in self.tiering_planner_intents.split(",") if n.strip()]
        return frozenset(names or DEFAULT_TIERING_INTENTS)

    @property
    def admin_origins(self) -> list[str]:
        return [o.strip() for o in self.admin_allowed_origins.split(",") if o.strip()]

    @property
    def admin_ips(self) -> list[str]:
        return [i.strip() for i in self.admin_allowed_ips.split(",") if i.strip()]

    @property
    def alert_emails(self) -> list[str]:
        return [e.strip() for e in self.alert_email_to.split(",") if e.strip()]

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
