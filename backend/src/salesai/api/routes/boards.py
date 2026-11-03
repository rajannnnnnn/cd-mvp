"""The founder's four boards (ADR 0028): admin application only. Aggregates; no customer content, no floor prices."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from salesai.api.deps import RT, Operator

router = APIRouter(prefix="/operator/boards", tags=["admin-boards"])
Days = Annotated[int, Query(ge=1, le=90, description="Look back this many days")]


@router.get("/sales", summary="Sales outcome: autonomy, lead to commitment, owner-confirmed wins, what the reply checks stopped")
async def sales(rt: RT, p: Operator, days: Days = 30) -> dict[str, Any]:
    return await rt.boards.sales(days)


@router.get("/growth", summary="Growth: signed up, number connected, first AI reply, first commitment, paid; trials, churn, support load")
async def growth(rt: RT, p: Operator, days: Days = 30) -> dict[str, Any]:
    return await rt.boards.growth(days)


@router.get("/money", summary="Money: MRR, ARPU, variable cost per shop split model and channel, margin per shop")
async def money(rt: RT, p: Operator, days: Days = 30) -> dict[str, Any]:
    return await rt.boards.money(days)


@router.get("/health", summary="Health: queues, dead letters, alerts, numbers needing attention, model and send failure rates")
async def health(rt: RT, p: Operator, days: Days = 1) -> dict[str, Any]:
    return await rt.boards.health(days)
