"""The agent audit (ADR 0037): admin application only. Aggregates and per-run records of decisions, cost and timing: never customer content."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from salesai.api.deps import RT, Operator

router = APIRouter(prefix="/operator/audit/agent", tags=["admin-audit"])
Days = Annotated[int, Query(ge=1, le=90)]


@router.get("/summary", summary="Agent audit: runs by outcome and trigger, total model cost, latency, how well turn detection waited")
async def summary(rt: RT, p: Operator, days: Days = 7) -> dict[str, Any]:
    return await rt.agent_audit.summary(days)


@router.get("/runs", summary="Agent runs, newest first, filterable")
async def runs(rt: RT, p: Operator, days: Days = 7, outcome: Annotated[str | None, Query(pattern="^(replied|reaction_only|no_reply|blocked|superseded|failed)$")] = None,
               trigger: Annotated[str | None, Query(pattern="^(turn_detection|follow_back|takeover)$")] = None, business_id: uuid.UUID | None = None,
               limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
    return await rt.agent_audit.runs(days, outcome=outcome, trigger=trigger, business_id=business_id, limit=limit)


@router.get("/runs/{run_id}", summary="One run: what woke it, each model call, the decision, the replies, and the time at each step")
async def run(run_id: uuid.UUID, rt: RT, p: Operator) -> dict[str, Any]:
    return await rt.agent_audit.run(run_id)
