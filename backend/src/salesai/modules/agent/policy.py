"""Turn policy: the model PROPOSES (PlannerOutput); code DECIDES. Converts the proposal plus pricing-engine
decisions into directives for the writer and into side effects (handoffs, gaps, deals, state) that are
committed only if the conversation has not moved on (INV-7). Every number a reply may contain originates
here, from the engine (INV-2)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from salesai.modules.agent import context as ctxmod
from salesai.modules.agent.models import PlannerOutput, ProductRef
from salesai.modules.pricing import Evaluation, PricingService
from salesai.modules.sales import next_stage


@dataclass
class DealReq:
    kind: str
    items: list[dict[str, Any]]
    details: dict[str, Any]
    value: Decimal | None


@dataclass
class Plan:
    intent: str
    directives: list[dict[str, Any]] = field(default_factory=list)
    reaction_emoji: str | None = None
    lead_stage: str = "new"
    lost_reason: str | None = None
    qualification: dict[str, Any] = field(default_factory=dict)
    selling_stopped: bool = False
    handoffs: list[tuple[str, str | None]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    deals: list[DealReq] = field(default_factory=list)
    evaluations: list[Evaluation] = field(default_factory=list)
    issued: set[Decimal] = field(default_factory=set)
    issued_percents: set[Decimal] = field(default_factory=set)
    real_urgency: set[str] = field(default_factory=set)
    allowed_texts: list[str] = field(default_factory=list)
    language: str = "en"
    script: str = "latin"
    sincere_identity_question: bool = False

    @property
    def silent(self) -> bool:
        return not self.directives and not self.reaction_emoji


def _issue(plan: Plan, ev: Evaluation) -> None:
    for v in ev.decision.values:
        plan.issued.add(v.amount)
        if v.kind == "discount_percent":
            plan.issued_percents.add(v.amount)
    for u in ev.decision.urgency:
        plan.real_urgency.add(u.kind)


def quote_directive(ev: Evaluation) -> dict[str, Any]:
    d = ev.decision
    names = ev.offer_names or {}
    cur = d.values[0].currency if d.values else "INR"
    return {
        "type": "quote", "decision": d.kind, "product": ev.product_name, "variant": ev.variant_name, "currency": cur,
        "semantics": d.semantics, "values": [{"kind": v.kind, "amount": f"{v.amount:f}"} for v in d.values],
        "applied_offers": [names.get(i, "special") for i in d.applied_offers], "free_items": list(d.free_items),
        "unmet": list(d.requires), "final_offer": not d.can_concede_more,
        "real_urgency": [{"kind": u.kind, "detail": u.detail} for u in d.urgency],
    }


def _price_of(ev: Evaluation) -> Decimal | None:
    return next((v.amount for v in ev.decision.values if v.kind == "price"), None)


async def build_plan(ctx: ctxmod.TurnContext, out: PlannerOutput, pricing: PricingService, *, trigger: str = "reply") -> Plan:
    bid, conv_id = ctx.business["id"], ctx.conv["id"]
    profile = ctx.business["profile"] or {}
    sales = ctx.business["sales_settings"] or {}
    qual: dict[str, Any] = dict(ctx.qualification)
    qual.update({k: v for k, v in out.qualification.items() if v not in (None, "")})
    plan = Plan(intent=out.intent, language=out.language, script=out.script,
                sincere_identity_question=out.sincere_identity_question)
    plan.selling_stopped = out.selling_stopped or ctx.conv["selling_stopped"]
    plan.lead_stage = next_stage(ctx.conv["lead_stage"], out.lead_stage, selling_stopped=plan.selling_stopped)
    plan.reaction_emoji = out.reaction_emoji if out.action in ("react_only", "reply") else None
    D = plan.directives
    valid_refs: list[ProductRef] = [r for r in out.product_refs if ctx.candidate(r.variant_id) is not None]

    async def evaluate(ref: ProductRef, ask: str) -> Evaluation:
        cand = ctx.candidate(ref.variant_id)
        assert cand is not None
        qualified = bool(qual.get("quantity") or qual.get("occasion") or qual.get("budget") or ref.quantity > 1)
        ev = await pricing.evaluate(bid, conv_id, ref.variant_id, ask=ask, counter=ref.counter_price, quantity=ref.quantity,  # type: ignore[arg-type]
                                    advance_payment=ref.advance_payment, repeat_customer=ctx.is_repeat, qualified=qualified,
                                    may_mention_offers=bool(sales.get("may_mention_offers", True)), now=ctx.now)
        plan.evaluations.append(ev)
        _issue(plan, ev)
        return ev

    def handoff(reason: str, note: str | None = None, notice: bool = True) -> None:
        plan.handoffs.append((reason, note))
        if notice:      # the writer gets a neutral label: it must not learn that a price floor exists
            D.append({"type": "handoff_notice", "reason": {"below_floor": "owner_review"}.get(reason, reason)})

    def focus(ref: ProductRef) -> None:
        qual["focus_variant_id"] = str(ref.variant_id)

    # --- identity honesty first (CR-7 / INV-9)
    if out.sincere_identity_question:
        D.append({"type": "identity"})
    elif out.greeted and out.intent != "greeting" and not ctx.has_prior_ai_message and out.action == "reply":
        D.append({"type": "greeting", "short": True})

    intent = out.intent
    if trigger == "nudge":
        cand = ctx.candidate(qual.get("focus_variant_id")) if qual.get("focus_variant_id") else None
        D.append({"type": "nudge", "product": cand["product_name"] if cand else None})
    elif intent == "greeting":
        D.append({"type": "greeting"})
    elif intent == "thanks":
        D.append({"type": "thanks"})
    elif intent == "goodbye":
        D.append({"type": "goodbye"})
    elif intent == "smalltalk":
        D.append({"type": "smalltalk"})
    elif intent == "out_of_scope":
        D.append({"type": "decline_out_of_scope"})
    elif intent == "unsupported_media":
        D.append({"type": "unsupported_media"})
    elif intent == "identity_question":
        pass
    elif intent == "not_interested":
        plan.selling_stopped, plan.lost_reason, plan.lead_stage = True, "not_interested", "lost"
        D.append({"type": "not_interested"})
    elif intent == "human_request":
        handoff("customer_asked_human", "Customer asked for a person")
    elif intent == "complaint":
        handoff("complaint", " ".join(m["body"] or "" for m in ctx.unanswered)[:300])
    elif intent in ("price_query", "discount_request", "counter_offer"):
        if not valid_refs and not qual.get("focus_variant_id"):
            D.append({"type": "ask_product"})
        else:
            refs = valid_refs or [ProductRef(variant_id=uuid.UUID(str(qual["focus_variant_id"])), quantity=int(qual.get("quantity") or 1))]
            ask = {"price_query": "price", "discount_request": "discount", "counter_offer": "counter"}[intent]
            for ref in refs[:3]:
                if ctx.candidate(ref.variant_id) is None:
                    continue
                ev = await evaluate(ref, ask)
                d = ev.decision
                focus(ref)
                if d.kind == "handoff":
                    handoff(d.handoff_reason or "other", d.note)
                elif d.kind == "ask_qualify":
                    D.append({"type": "ask_qualify", "product": ev.product_name, "variant": ev.variant_name,
                              "fields": ["quantity"] if not qual.get("quantity") else ["occasion"]})
                else:
                    D.append(quote_directive(ev))
                    if ask != "price" or d.state.step:
                        qual["price_quoted"] = True
                    qual["price_quoted"] = True
                    if d.kind == "accept":
                        price = _price_of(ev)
                        qual["pending_order"] = {"variant_id": str(ref.variant_id), "quantity": ref.quantity, "price": str(price)}
                        plan.lead_stage = next_stage(plan.lead_stage, "ready_to_buy")
                        D.append(_order_summary(ev, ref.quantity, not qual.get("delivery_address")))
    elif intent in ("order_intent", "affirmation") and (out.commitment or qual.get("pending_order")):
        await _order_flow(ctx, out, plan, qual, valid_refs, evaluate, handoff, sales)
    elif intent == "visit_intent":
        c = out.commitment
        if c and c.visit_time:
            plan.deals.append(DealReq("visit", [], {"time": c.visit_time}, None))
            D.append({"type": "visit_captured", "time": c.visit_time})
            plan.lead_stage = next_stage(plan.lead_stage, "ready_to_buy")
        else:
            D.append({"type": "visit_ask_time"})
        for ref in valid_refs[:1]:
            focus(ref)
    elif intent == "business_info":
        answered, missing = 0, []
        for topic in out.info_topics or ["other"]:
            txt = ctxmod.topic_text(profile, topic)
            if txt:
                D.append({"type": "info", "topic": topic, "text": txt})
                plan.allowed_texts.append(txt)
                answered += 1
            else:
                missing.append(topic)
        if missing:
            q = " ".join(m["body"] or "" for m in ctx.unanswered)[:400]
            plan.gaps.append(q)
            handoff("unknown_answer", f"Missing business info: {', '.join(missing)}", notice=True)
    elif intent in ("product_inquiry", "availability"):
        if valid_refs:
            for ref in valid_refs[:2]:
                cand = ctx.candidate(ref.variant_id)
                assert cand is not None
                focus(ref)
                D.append({"type": "availability" if intent == "availability" else "product_info", "product": cand["product_name"],
                          "variant": cand["variant_name"], "description": cand.get("description"), "availability": cand["availability"],
                          "attributes": cand.get("attributes") or {}})
                plan.allowed_texts += [str(cand.get("description") or ""), str(cand.get("attributes") or ""), cand["product_name"]]
        else:
            names = list(dict.fromkeys(c["product_name"] for c in ctx.candidates))[:6]
            if names:
                D.append({"type": "catalog", "items": names})
                plan.allowed_texts += names
            else:
                handoff("unknown_answer", "Customer asked about products; catalog is empty", notice=True)
    elif intent == "affirmation":
        D.append({"type": "ack"})
    else:   # unknown
        q = out.knowledge_question or " ".join(m["body"] or "" for m in ctx.unanswered)[:400]
        facts = ctxmod.matching_facts(profile, q) if q else []
        if facts:
            D.append({"type": "facts", "items": facts})
            plan.allowed_texts += facts
        elif out.action == "handoff" or out.handoff_reason == "unknown_answer" or q:
            plan.gaps.append(q)
            handoff("unknown_answer", f"Customer question: {q[:200]}", notice=True)
        else:
            D.append({"type": "ack"})

    plan.qualification = qual
    plan.allowed_texts.append(str(profile))
    for c in ctx.candidates:
        plan.allowed_texts += [c["product_name"], str(c.get("description") or ""), str(c.get("attributes") or ""), c["variant_name"], str(c.get("category") or "")]
    return plan


def _order_summary(ev: Evaluation, qty: int, needs_address: bool) -> dict[str, Any]:
    vals = {v.kind: v.amount for v in ev.decision.values}
    price = vals.get("price")
    return {"type": "order_summary", "product": ev.product_name, "variant": ev.variant_name, "quantity": qty,
            "price": f"{price:f}" if price is not None else None,
            "total": f"{vals['total']:f}" if "total" in vals else (f"{price * qty:f}" if price is not None and qty > 1 else None),
            "needs_address": needs_address, "currency": ev.decision.values[0].currency if ev.decision.values else "INR"}


async def _order_flow(ctx, out, plan, qual, valid_refs, evaluate, handoff, sales) -> None:  # noqa: ANN001
    D = plan.directives
    c = out.commitment
    pending = qual.get("pending_order")
    vid = (c.variant_id if c and c.variant_id else None) or (valid_refs[0].variant_id if valid_refs else None) or (pending or {}).get("variant_id")
    if vid is None or ctx.candidate(vid) is None:
        D.append({"type": "ask_product"})
        return
    cand = ctx.candidate(vid)
    qty = (c.quantity if c else None) or (pending or {}).get("quantity") or int(qual.get("quantity") or 1)
    ref = ProductRef(variant_id=uuid.UUID(str(vid)), quantity=int(qty))
    qual["focus_variant_id"] = str(vid)
    if cand["availability"] == "out_of_stock":
        D.append({"type": "availability", "product": cand["product_name"], "variant": cand["variant_name"], "availability": "out_of_stock"})
        return
    ev = await evaluate(ref, "price")
    d = ev.decision
    if d.kind == "handoff":
        handoff(d.handoff_reason or "other", d.note)
        return
    if d.kind == "ask_qualify":
        D.append({"type": "ask_qualify", "product": ev.product_name, "variant": ev.variant_name, "fields": ["quantity"]})
        return
    price = _price_of(ev)
    if price is None:        # range / from: no single price to commit to
        D.append(quote_directive(ev))
        return
    qual["pending_order"] = {"variant_id": str(vid), "quantity": int(qty), "price": str(price)}
    plan.lead_stage = next_stage(plan.lead_stage, "ready_to_buy")
    address = (c.delivery_address if c else None) or qual.get("delivery_address")
    if address:
        qual["delivery_address"] = address
    confirmed = bool(c and c.confirmed) and bool(address)
    if confirmed:
        total = price * int(qty)
        items = [{"variant_id": str(vid), "name": cand["product_name"], "variant": cand["variant_name"], "qty": int(qty), "agreed_price": str(price)}]
        plan.deals.append(DealReq("order", items, {"address": address}, total))
        threshold = Decimal(str(sales.get("handoff_value_threshold", 0) or 0))
        if threshold and total >= threshold:
            plan.handoffs.append(("high_value", f"Order value {total} (threshold {threshold})"))
        qual.pop("pending_order", None)
        D.append({"type": "order_captured"})
    else:
        D.append(_order_summary(ev, int(qty), not address))
