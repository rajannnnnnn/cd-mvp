"""Context assembly (Technical Design: LLM orchestration). Built from TENANT data only, through the tenant
role: business profile, relevant catalog (public view), conversation, lead state, customer style and owner
voice examples. Prices and disclosure modes are stripped here: the model never sees a number it could
repeat — numbers only reach the writer through pricing-engine directives (INV-1, INV-2)."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from salesai.db import Database
from salesai.modules.catalog import repo as catalog

PLANNER_KEYS = ("variant_id", "product_id", "product_name", "variant_name", "description", "category",
                "attributes", "product_attributes", "availability")


_AMOUNT = re.compile(r"(?:₹|rs\.?\s?|inr\s?)\s?\d[\d,]*(?:\.\d+)?(?:\s?/-)?|\b\d[\d,]*(?:\.\d+)?\s?(?:/-|rupees?|rupaye|rs\b)|\b\d{3,}(?:,\d{2,3})*(?:\.\d+)?\b", re.I)


def redact_amounts(text: str | None) -> str | None:
    """Past prices stated by the assistant/owner are not model input: the planner needs to understand the customer,
    not to repeat numbers. (Strengthens INV-1: a floor-valued final quote never re-enters planner context.)"""
    return _AMOUNT.sub("[amount]", text) if text else text


@dataclass
class TurnContext:
    business: dict[str, Any]
    conv: dict[str, Any]
    customer: dict[str, Any]
    number: dict[str, Any]
    unanswered: list[dict[str, Any]]
    history: list[dict[str, Any]]
    candidates: list[dict[str, Any]]            # includes prices: NEVER serialized into model input
    style_examples: list[dict[str, Any]]
    is_repeat: bool
    has_prior_ai_message: bool
    now: datetime
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def qualification(self) -> dict[str, Any]:
        return self.conv.get("qualification") or {}

    def candidate(self, variant_id: uuid.UUID | str) -> dict[str, Any] | None:
        return next((c for c in self.candidates if str(c["variant_id"]) == str(variant_id)), None)

    def planner_input(self, trigger: str = "reply") -> dict[str, Any]:
        profile = self.business["profile"] or {}
        return {
            "trigger": trigger,
            "now": self.now.isoformat(),
            "business": {"name": self.business["name"], "profile": _public_profile(profile),
                         "sales": {k: v for k, v in (self.business["sales_settings"] or {}).items() if k != "handoff_value_threshold"}},
            "candidates": [{k: (str(c[k]) if k in ("variant_id", "product_id") else c.get(k)) for k in PLANNER_KEYS} for c in self.candidates],
            "conversation": {
                "lead_stage": self.conv["lead_stage"],
                "qualification": _public_qualification(self.qualification),
                "summary": self.conv.get("summary"),
                "history": [{"sender": m["sender"], "kind": m["kind"],
                             "body": m["body"] if m["sender"] == "customer" else redact_amounts(m["body"])} for m in self.history],
            },
            "unanswered": [{"id": str(m["id"]), "kind": m["kind"], "text": m["body"]} for m in self.unanswered],
            "customer": {"name": self.customer.get("name"), "style": self.customer.get("profile") or {}},
        }


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in profile.items()}


def _public_qualification(q: dict[str, Any]) -> dict[str, Any]:
    """Pending-order price is a number the engine issued earlier; it is not a model input."""
    out = dict(q)
    if isinstance(out.get("pending_order"), dict):
        out["pending_order"] = {k: v for k, v in out["pending_order"].items() if k != "price"}
    return out


async def load(db: Database, business_id: uuid.UUID, conversation_id: uuid.UUID, now: datetime) -> TurnContext:
    async with db.tenant(business_id) as c:
        business = await (await c.execute("SELECT * FROM businesses WHERE id=%s", (business_id,))).fetchone()
        conv = await (await c.execute("SELECT * FROM conversations WHERE id=%s", (conversation_id,))).fetchone()
        customer = await (await c.execute("SELECT * FROM customers WHERE id=%s", (conv["customer_id"],))).fetchone()
        number = await (await c.execute("SELECT * FROM whatsapp_numbers WHERE id=%s", (conv["whatsapp_number_id"],))).fetchone()
        unanswered = await (await c.execute(
            """SELECT id, kind, body, wa_message_id, created_at FROM messages
               WHERE conversation_id=%s AND direction='in' AND NOT answered ORDER BY created_at, id""", (conversation_id,))).fetchall()
        n = int((business["conversation_settings"] or {}).get("history_messages", 20))
        history = list(reversed(await (await c.execute(
            """SELECT id, sender, body, kind, created_at FROM messages WHERE conversation_id=%s AND (answered OR direction='out')
               ORDER BY created_at DESC, id DESC LIMIT %s""", (conversation_id, n))).fetchall()))
        recent_customer = " ".join([m["body"] or "" for m in unanswered] + [m["body"] or "" for m in history if m["sender"] == "customer"][-2:])
        candidates = await catalog.retrieve(c, recent_customer, limit=8)
        focus = (conv["qualification"] or {}).get("focus_variant_id")
        if focus and not any(str(x["variant_id"]) == str(focus) for x in candidates):
            extra = await (await c.execute("SELECT * FROM catalog_public WHERE variant_id=%s", (focus,))).fetchone()
            if extra:
                candidates.append(extra)
        style = await (await c.execute("SELECT situation, customer_text, owner_reply FROM style_examples ORDER BY created_at DESC LIMIT 8")).fetchall()
        repeat = await (await c.execute(
            """SELECT 1 FROM deals d JOIN conversations cv ON cv.id = d.conversation_id
               WHERE cv.customer_id=%s AND d.status='won' LIMIT 1""", (customer["id"],))).fetchone()
        prior_ai = await (await c.execute(
            "SELECT 1 FROM messages WHERE conversation_id=%s AND direction='out' AND sender='ai' LIMIT 1", (conversation_id,))).fetchone()
    return TurnContext(business=business, conv=conv, customer=customer, number=number, unanswered=unanswered, history=history,
                       candidates=candidates, style_examples=style, is_repeat=repeat is not None, has_prior_ai_message=prior_ai is not None, now=now)


# ---------------------------------------------------------------- business profile -> text for directives
DAYS = [("mon", "Mon"), ("tue", "Tue"), ("wed", "Wed"), ("thu", "Thu"), ("fri", "Fri"), ("sat", "Sat"), ("sun", "Sun")]


def hours_text(profile: dict[str, Any]) -> str | None:
    h = profile.get("hours") or profile.get("hours_text")
    if not h:
        return None
    if isinstance(h, str):
        return h
    if isinstance(h, dict):
        parts = []
        for k, label in DAYS:
            v = h.get(k)
            if v:
                parts.append(f"{label} {v if isinstance(v, str) else ', '.join(v)}")
        return "; ".join(parts) or None
    return None


def topic_text(profile: dict[str, Any], topic: str) -> str | None:
    def s(v: Any) -> str | None:
        if not v:
            return None
        return v if isinstance(v, str) else ", ".join(str(x) for x in v)

    if topic == "hours":
        return hours_text(profile)
    if topic == "address":
        return s(profile.get("address"))
    if topic == "delivery":
        d, areas = s(profile.get("delivery") or profile.get("delivery_info")), s(profile.get("delivery_areas"))
        if d and areas:
            return f"{d} Areas: {areas}"
        return d or (f"We deliver to: {areas}" if areas else None)
    if topic == "payment":
        return s(profile.get("payment_modes") or profile.get("payment"))
    if topic == "returns":
        return s(profile.get("returns") or profile.get("return_policy") or profile.get("policies"))
    if topic == "contact":
        return s(profile.get("phone") or profile.get("contact"))
    return None


def matching_facts(profile: dict[str, Any], question: str) -> list[str]:
    from salesai.modules.catalog.repo import query_tokens
    toks = set(query_tokens(question))
    if not toks:
        return []
    out = []
    for f in profile.get("facts") or []:
        if toks & set(query_tokens(str(f))):
            out.append(str(f))
    return out[:3]
