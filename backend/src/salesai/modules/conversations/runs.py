"""The agent audit's writer (ADR 0037): one `agent_runs` row each time the AI is woken. Started when the run starts and finished when it
ends, so a run killed halfway is still visible (the recovery sweep marks it interrupted). Recording must never take down the agent:
a failure here is logged and the turn carries on."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from salesai.db import Database, jsonb
from salesai.modules.agent import TurnOutcome

log = logging.getLogger("salesai.runs")


def outcome_of(o: TurnOutcome) -> tuple[str, str | None]:
    """How a run ended, in the founder's words, and why."""
    if o.status == "committed":
        return ("reaction_only" if o.reaction_only else "replied"), (o.route.get("fallback") or None)
    if o.status == "silent":
        return "no_reply", f"the AI chose not to reply ({o.route.get('intent') or 'no intent'})"
    return o.status if o.status in ("blocked", "superseded") else "failed", o.reason


async def start_run(db: Database, bid: uuid.UUID, cid: uuid.UUID, trigger: str, detail: dict[str, Any],
                    first_inbound_at: datetime | None = None) -> uuid.UUID | None:
    try:
        async with db.tenant(bid) as c:
            row = await (await c.execute(
                "INSERT INTO agent_runs (business_id, conversation_id, trigger, trigger_detail, first_inbound_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (bid, cid, trigger, jsonb(detail), first_inbound_at))).fetchone()
        return row["id"] if row else None
    except Exception:  # noqa: BLE001
        log.exception("could not record the start of an agent run")
        return None


async def finish_run(db: Database, bid: uuid.UUID, run_id: uuid.UUID | None, *, outcome: str, reason: str | None = None,
                     turn_id: uuid.UUID | None = None, route: dict[str, Any] | None = None, llm: dict[str, Any] | None = None) -> None:
    if run_id is None:
        return
    try:
        async with db.tenant(bid) as c:
            await c.execute(
                """UPDATE agent_runs SET status='finished', outcome=%s, reason=%s, turn_id=%s, route=%s, llm=%s, finished_at=now()
                    WHERE id=%s AND status='running'""",
                (outcome, (reason or "")[:300] or None, turn_id, jsonb(route or {}), jsonb(llm or {}), run_id))
    except Exception:  # noqa: BLE001
        log.exception("could not record the end of an agent run")


async def finish_with(db: Database, bid: uuid.UUID, run_id: uuid.UUID | None, o: TurnOutcome) -> None:
    outcome, reason = outcome_of(o)
    await finish_run(db, bid, run_id, outcome=outcome, reason=reason, turn_id=o.turn_id, route=o.route, llm=o.llm)


async def record_instant(db: Database, bid: uuid.UUID, cid: uuid.UUID, trigger: str, detail: dict[str, Any], outcome: str, reason: str,
                         first_inbound_at: datetime | None = None) -> None:
    """A run that ended before the model was involved: the AI was not allowed to answer, or a guard stopped it. No reply, and the audit says why."""
    run = await start_run(db, bid, cid, trigger, detail, first_inbound_at)
    await finish_run(db, bid, run, outcome=outcome, reason=reason)
