"""Catalog and pricing input/output models. Validation mirrors the DB CHECK constraints so owners get a
useful error before the database refuses. Floor prices are WRITE-ONLY (INV-1): they exist on input
models only and never on any output model."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Disclosure = Literal["fixed", "range", "starts_from", "after_qualifying", "on_request"]
Availability = Literal["in_stock", "limited", "made_to_order", "out_of_stock"]
Money = Decimal


class RequirementIn(BaseModel):
    type: Literal["quantity", "advance_payment", "repeat_customer"]
    min: int | None = Field(default=None, ge=2, le=1000)


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disclosure: Disclosure
    currency: str = Field(default="INR", pattern="^[A-Z]{3}$")
    list_price: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    range_min: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    range_max: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    negotiable: bool = False
    ai_may_negotiate: bool = False
    concession_steps: int = Field(default=0, ge=0, le=10)
    concession_requires: list[RequirementIn] = []
    round_to: Money = Field(default=Decimal("1"), gt=0, max_digits=12, decimal_places=2)
    # WRITE-ONLY. Never returned by any endpoint, never logged.
    floor_price: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2, repr=False)
    clear_floor: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        d = self.disclosure
        if d in ("fixed", "starts_from") and self.list_price is None:
            raise ValueError(f"disclosure '{d}' needs a list price")
        if d == "range" and (self.range_min is None or self.range_max is None or self.range_min > self.range_max):
            raise ValueError("disclosure 'range' needs range_min <= range_max")
        if self.negotiable and self.list_price is None:
            raise ValueError("a negotiable item needs a list price")
        if self.ai_may_negotiate and not self.negotiable:
            raise ValueError("AI negotiation requires the item to be negotiable")
        if self.ai_may_negotiate and self.concession_steps < 1:
            raise ValueError("AI negotiation needs at least one concession step")
        if self.floor_price is not None and self.list_price is not None and self.floor_price > self.list_price:
            raise ValueError("floor price cannot be above the list price")
        if self.floor_price is not None and self.clear_floor:
            raise ValueError("set a floor or clear it, not both")
        return self


class PolicyOut(BaseModel):
    disclosure: Disclosure
    currency: str
    list_price: Money | None
    range_min: Money | None
    range_max: Money | None
    negotiable: bool
    ai_may_negotiate: bool
    concession_steps: int
    concession_requires: list[dict[str, Any]]
    round_to: Money
    floor_set: bool          # whether a floor exists; the VALUE is never exposed


class VariantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="default", min_length=1, max_length=120)
    attributes: dict[str, Any] = {}
    availability: Availability = "in_stock"
    stock_qty: int | None = Field(default=None, ge=0)
    is_default: bool = False
    active: bool = True
    policy: PolicyIn


class VariantOut(BaseModel):
    id: uuid.UUID
    name: str
    attributes: dict[str, Any]
    availability: Availability
    stock_qty: int | None
    is_default: bool
    active: bool
    policy: PolicyOut | None


class ProductIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=120)
    attributes: dict[str, Any] = {}
    active: bool = True
    variants: list[VariantIn] = Field(min_length=1)

    @field_validator("variants")
    @classmethod
    def _one_default(cls, v: list[VariantIn]) -> list[VariantIn]:
        if not any(x.is_default for x in v):
            v[0].is_default = True
        return v


class ProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    category: str | None = None
    attributes: dict[str, Any] | None = None
    active: bool | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    category: str | None
    attributes: dict[str, Any]
    active: bool
    variants: list[VariantOut]
    created_at: datetime


class OfferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["percent", "flat", "bundle", "free_item"]
    value: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    free_item: str | None = Field(default=None, max_length=200)
    variant_id: uuid.UUID | None = None
    conditions: dict[str, Any] = {}
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    may_cross_floor: bool = False
    active: bool = True

    @model_validator(mode="after")
    def _ok(self) -> Self:
        if self.kind in ("percent", "flat", "bundle") and self.value is None:
            raise ValueError(f"offer kind '{self.kind}' needs a value")
        if self.kind == "percent" and self.value is not None and self.value > 100:
            raise ValueError("percent offer cannot exceed 100")
        if self.kind == "free_item" and not self.free_item:
            raise ValueError("free_item offer needs the item name")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("offer must end after it starts")
        return self


class OfferOut(OfferIn):
    id: uuid.UUID
    created_at: datetime
