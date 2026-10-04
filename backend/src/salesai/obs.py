"""Structured logs and metrics. Every record carries business_id / conversation_id / turn_id
(NFR-13). Tokens and phone numbers are redacted by default (HLD Security)."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

_ctx: contextvars.ContextVar[Mapping[str, Any]] = contextvars.ContextVar("log_ctx", default=MappingProxyType({}))

PHONE_RE = re.compile(r"\+?\d{10,15}")
SECRET_RE = re.compile(r"(?i)(bearer\s+[a-z0-9._\-]+|eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-.]+|sk-[a-z0-9\-_]{10,})")


def redact(text: str) -> str:
    text = SECRET_RE.sub("[redacted-secret]", text)
    return PHONE_RE.sub(lambda m: m.group(0)[:3] + "***" + m.group(0)[-2:], text)


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    token = _ctx.set({**_ctx.get(), **{k: str(v) for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _ctx.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_ctx.get())


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            **_ctx.get(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            data.update({k: (redact(v) if isinstance(v, str) else v) for k, v in extra.items()})
        if record.exc_info:
            data["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, default=str)


def setup_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    logger.log(level, msg, extra={"fields": fields})


# ---- metrics (Prometheus-style)
QUEUE_DEPTH = Gauge("salesai_queue_depth", "Pending jobs", ["queue"])
QUEUE_OLDEST_AGE = Gauge("salesai_queue_oldest_age_seconds", "Age of oldest due job", ["queue"])
QUEUE_DEAD = Gauge("salesai_queue_dead", "Dead-lettered jobs", ["queue"])
JOBS_TOTAL = Counter("salesai_jobs_total", "Jobs processed", ["queue", "kind", "outcome"])
JOB_SECONDS = Histogram("salesai_job_seconds", "Job handler duration", ["queue", "kind"])
WEBHOOK_SECONDS = Histogram("salesai_webhook_seconds", "Webhook ingress duration",
                            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1))
TURN_SECONDS = Histogram("salesai_turn_seconds", "End-of-turn decision to first outbound enqueue",
                         buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16))
LLM_CALLS = Counter("salesai_llm_calls_total", "LLM calls", ["provider", "stage", "outcome"])
LLM_COST_MICROS = Counter("salesai_llm_cost_micros_total", "LLM cost (1e-6 INR)", ["business_id"])
VALIDATOR_REJECTIONS = Counter("salesai_reply_check_rejections_total", "Reply checks failed", ["check"])
FLOOD_GUARD = Counter("salesai_flood_guard_total", "Turns not answered because a conversation exceeded its turn budget")
SEND_RESULTS = Counter("salesai_send_total", "Outbound sends", ["outcome"])
