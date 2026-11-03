"""Rules before models (ADR 0039). The planner stage tries the deterministic rules first and keeps their answer only when they are sure and the
case is safe for rules; everything else goes to the language model unchanged. A rule never decides anything about money (prices come from the
pricing engine whatever tier ran), negotiation, commitments or anything unclear. The tier that answered is recorded in the audit: a rules
answer carries the provider name "local", zero tokens and zero cost."""
from __future__ import annotations

import re
from typing import Any

from salesai.config import DEFAULT_TIERING_INTENTS
from salesai.modules.agent.llm.base import LLMError, LLMProvider, LLMRequest, LLMResult, T
from salesai.modules.agent.llm.local_nlu import norm

DEFAULT_RULES_INTENTS = frozenset(DEFAULT_TIERING_INTENTS)
VARIANT_INTENTS = frozenset({"price_query", "availability", "product_inquiry"})     # about a product: the rules must be sure WHICH product


def _phrase_in(text: str, phrase: str) -> bool:
    p = norm(phrase)
    return len(p) >= 4 and re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", text) is not None


def _names(c: dict[str, Any]) -> list[str]:
    return [c.get("product_name") or "", *[str(a) for a in ((c.get("product_attributes") or {}).get("aliases") or [])]]


def exact_variant(inp: dict[str, Any], out: Any) -> bool:
    """The rules picked a product. They are only trusted when the customer's own words name it (its name or an alias, whole words), or it is the
    product already being discussed and the message names no other. A category word, a colour, a typo or no product at all is a judgement call."""
    if not out.product_refs:
        return False
    cands: list[dict[str, Any]] = inp.get("candidates") or []
    vid = str(out.product_refs[0].variant_id)
    chosen = next((c for c in cands if str(c["variant_id"]) == vid), None)
    if chosen is None:
        return False
    text = norm(" . ".join(m.get("text") or "" for m in inp.get("unanswered", [])))
    mentions = {str(c["product_id"]) for c in cands if any(_phrase_in(text, n) for n in _names(c))}
    if str(chosen["product_id"]) in mentions:
        return mentions == {str(chosen["product_id"])}               # named, and nothing else is
    focus = ((inp.get("conversation") or {}).get("qualification") or {}).get("focus_variant_id")
    return not mentions and focus is not None and str(focus) == vid


class TieredPlanner:
    name = "tiered"

    def __init__(self, llm: LLMProvider, rules: LLMProvider, *, intents: frozenset[str] = DEFAULT_RULES_INTENTS, enabled: bool = True):
        self.llm, self.rules, self.intents, self.enabled = llm, rules, frozenset(intents), enabled

    @property
    def providers(self) -> list[LLMProvider]:
        """The model route behind the rules, as the other routes expose it (the routing tests and the founder's tools look here)."""
        return list(getattr(self.llm, "providers", [self.llm]))

    async def generate(self, req: LLMRequest, schema: type[T]) -> LLMResult[T]:
        if req.stage != "planner" or not self.enabled or not self.intents:
            return await self.llm.generate(req, schema)
        try:
            res = await self.rules.generate(req, schema)
        except LLMError:
            return await self.llm.generate(req, schema)
        out: Any = res.output
        if out.intent in self.intents and (out.intent not in VARIANT_INTENTS or exact_variant(req.input, out)):
            return res                                                # decided by rules: no model call, no cost, instant
        return await self.llm.generate(req, schema)
