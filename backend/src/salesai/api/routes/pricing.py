from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from salesai.api.deps import RT, Owner
from salesai.modules.pricing.engine import concession_levels, q2

router = APIRouter(tags=["pricing"])


class LadderIn(BaseModel):
    list_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    floor_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2, repr=False)   # write-only: used, never echoed
    concession_steps: int = Field(ge=1, le=10)
    round_to: Decimal = Field(default=Decimal("1"), gt=0, max_digits=12, decimal_places=2)


class LadderOut(BaseModel):
    list_price: Decimal
    steps: list[Decimal | None] = Field(description="The prices the assistant would offer, in order. The last step is your floor and is returned as null: floors are never sent back.")


@router.post("/pricing/ladder", response_model=LadderOut, summary="Preview the concession ladder with the real pricing engine (the floor is never returned)")
async def ladder(body: LadderIn, rt: RT, p: Owner) -> Any:
    levels = concession_levels(q2(body.list_price), q2(body.floor_price), body.concession_steps, body.round_to)
    steps: list[Decimal | None] = [lv for lv in levels[:-1]]
    if levels:
        steps.append(None)        # the final level IS the floor
    return {"list_price": q2(body.list_price), "steps": steps}
