"""Owner configuration by chat: structured changes extracted from the owner's message (FR-CF-8)."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConfigOp(BaseModel):
    model_config = ConfigDict(extra="ignore")
    op: Literal["set_profile", "add_fact", "add_product", "set_price"]
    field: Literal["address", "hours", "delivery", "payment_modes", "returns", "phone"] | None = None
    value: str | None = Field(default=None, max_length=1000)
    name: str | None = Field(default=None, max_length=200)
    product: str | None = Field(default=None, max_length=200)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=120)


class ConfigChanges(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ops: list[ConfigOp] = []
    clarification: str | None = None
