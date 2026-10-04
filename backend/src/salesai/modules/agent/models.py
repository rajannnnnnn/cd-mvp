"""Structured contracts between code and the model. Model output that drives actions is always
schema-validated (never parsed from free text). The model PROPOSES; code validates and executes."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "greeting", "product_inquiry", "price_query", "discount_request", "counter_offer", "availability",
    "business_info", "order_intent", "visit_intent", "affirmation", "complaint", "human_request", "out_of_scope",
    "unsupported_media", "not_interested", "identity_question", "thanks", "goodbye", "smalltalk", "unknown", "nudge"]
Action = Literal["reply", "silent", "handoff", "react_only"]
Stage = Literal["new", "exploring", "interested", "negotiating", "ready_to_buy", "won", "lost"]
Lang = Literal["en", "hi", "hinglish"]
Script = Literal["latin", "devanagari"]
Topic = Literal["hours", "address", "delivery", "payment", "returns", "contact", "other"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ProductRef(_M):
    variant_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=100000)
    counter_price: Decimal | None = Field(default=None, ge=0)     # a price the CUSTOMER proposed
    advance_payment: bool = False


class Commitment(_M):
    kind: Literal["order", "visit"]
    variant_id: uuid.UUID | None = None
    quantity: int = Field(default=1, ge=1)
    delivery_address: str | None = None
    visit_time: str | None = None
    confirmed: bool = False


class PlannerOutput(_M):
    intent: Intent
    action: Action = "reply"
    lead_stage: Stage = "new"
    language: Lang = "en"
    script: Script = "latin"
    formality: Literal["casual", "neutral", "formal"] = "neutral"
    ask: Literal["price", "discount", "counter", "none"] = "none"
    product_refs: list[ProductRef] = []
    qualification: dict[str, Any] = {}
    commitment: Commitment | None = None
    info_topics: list[Topic] = []
    knowledge_question: str | None = None
    greeted: bool = False
    sincere_identity_question: bool = False
    wants_human: bool = False
    complaint: bool = False
    selling_stopped: bool = False
    reaction_emoji: str | None = None
    handoff_reason: Literal["unknown_answer", "on_request_price", "below_floor", "customer_asked_human",
                            "complaint", "high_value", "other"] | None = None
    rationale: str = ""


class WriterOutput(_M):
    parts: list[str] = Field(min_length=1, max_length=4)
    reaction_emoji: str | None = None


class CheckOutput(_M):
    scope_ok: bool = True
    claims_human: bool = False
    invented_claims: list[str] = []
    issues: list[str] = []


def schema_of(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema handed to the provider for structured output."""
    s = model.model_json_schema()
    return s
