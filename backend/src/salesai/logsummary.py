"""One-line summaries for the logs. Plain words in a fixed order, so a person can read a running service at a glance and a search for one word finds one
kind of event. Nothing a customer wrote, no prices, no phone numbers, no secrets: only what the system did and how long it took."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def _db(url: str) -> str:
    u = urlsplit(url)
    tls = "encrypted" if "sslmode=require" in (u.query or "") or "sslmode=verify" in (u.query or "") else "NOT encrypted"
    return f"{u.hostname}:{u.port or 5432}{u.path} as {u.username} ({tls})"


def startup_line(s: Any, role: str) -> str:
    """What this part is, where it listens, and what it talks to. Shown once when it starts."""
    bits = [f"ChatDeal {role} started", f"environment {s.env}", f"listens on :{s.http_port}", f"database {_db(s.database_url)}"]
    if role in ("worker", "all"):
        bits.append(f"queues {','.join(s.queues)} ({s.queue_backend})")
    if s.uses_model:
        models = {st: getattr(s, f"llm_model_{st}") for st in ("planner", "writer", "check")}
        bits.append(f"model {s.llm_provider}: " + ", ".join(f"{st} {m}" for st, m in models.items()))
    return " | ".join(bits)


def _secs(ms: Any) -> str:
    return f"{float(ms) / 1000:.1f}s"


def turn_line(outcome: Any, eot_reason: str | None, waited_ms: int | None) -> str:
    """What happened to a customer's messages in one turn: answered or why not, how many model calls, how long each took, what it cost."""
    llm = getattr(outcome, "llm", None) or {}
    calls, failed = int(llm.get("calls", 0)), int(llm.get("failed", 0))
    stages = " ".join(f"{name} {_secs(st.get('ms', 0))}" for name, st in (llm.get("by_stage") or {}).items() if st.get("ms"))
    cost = f"₹{int(llm.get('cost_micros', 0)) / 1_000_000:.3f}"
    if calls == 0:
        model = "no model call"
    else:
        model = f"{calls} model call{'s' if calls != 1 else ''}" + (f" ({stages})" if stages else "") + f" {_secs(llm.get('llm_ms', 0))} {cost}"
        if failed:
            model += f", {failed} failed"
    status = outcome.status
    if status == "committed":
        n = len(outcome.parts)
        what = f", {n} message{'s' if n != 1 else ''}" if n else (", a reaction only" if outcome.reaction_only else "")
        head = "replied" + (f" intent={outcome.intent}" if outcome.intent else "") + what
    elif status == "silent":
        head = "chose not to reply" + (f" intent={outcome.intent}" if outcome.intent else "")
    elif status == "superseded":
        head = f"discarded (work wasted): {outcome.reason or 'the conversation moved on'}"
    else:
        head = f"{status}: {outcome.reason or ''}".rstrip(": ")
    wait = f"waited {_secs(waited_ms)} for the customer to finish ({eot_reason})" if waited_ms is not None and eot_reason else ""
    return " | ".join(x for x in (head, model, wait) if x)
