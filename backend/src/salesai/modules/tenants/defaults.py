"""Seed values for per-tenant settings. After creation these live in the database and are tunable per
business; nothing in the engine reads these constants at runtime (Technical Design: never constants)."""
from __future__ import annotations

from typing import Any

CONVERSATION_SETTINGS: dict[str, Any] = {
    "first_check_ms": 1200,              # first end-of-turn check after a customer message
    "max_wait_ms": 12000,                # never wait longer than this to batch messages
    "min_quiet_complete_ms": 1200,       # quiet time needed when the last message looks complete
    "min_quiet_incomplete_ms": 3500,     # ... when it looks unfinished
    "default_gap_ms": 4000,              # assumed customer gap until history exists
    "owner_pause_minutes": 120,          # AI pause after the owner replies manually (FR-CV-9)
    "business_hours_behavior": "reply_normally",   # reply_normally | slower | wait_for_open
    "nudge_after_minutes": 240,          # one in-window nudge after the customer goes quiet (FR-SL-8)
    "history_messages": 20,              # messages given to the model before summarising
    "daily_summary_hour": 21,            # local hour at which the owner gets the daily summary (FR-RP-1)
}

TIMING_PARAMS: dict[str, Any] = {
    # log-normal priors (ms); replaced per business as owner echo data accumulates
    "read_delay": {"mu": 7.6, "sigma": 0.5, "min_ms": 800, "max_ms": 6000},
    "typing_ms_per_char": {"mu": 3.9, "sigma": 0.25, "min_ms": 25, "max_ms": 140},
    "part_gap": {"mu": 7.2, "sigma": 0.5, "min_ms": 600, "max_ms": 4000},
    "max_total_delay_ms": 20000,         # pacing never costs a sale
    "slower_multiplier": 2.5,
}

SALES_SETTINGS: dict[str, Any] = {
    "proactiveness": "medium",           # low | medium | high
    "may_mention_offers": True,
    "honorific": "ji",                   # appended to greetings in Hindi/Hinglish
    "handoff_value_threshold": 50000,    # order value that always alerts the owner (high_value)
    "language_default": "en",
}

LIMITS: dict[str, Any] = {
    "max_concurrent_turns": 2,
    "llm_tokens_per_day": 500_000,
    "outbound_per_day": 2_000,
}
