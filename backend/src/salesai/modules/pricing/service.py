"""DB-backed pricing: the only code path that reads price_floors (via the app_pricing role, INV-1).
Everything numeric a customer may hear comes out of `PricingService.evaluate` (INV-2)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from salesai.db import Database, jsonb
from salesai.modules.pricing.engine import (
    Ask,
    Decision,
    Facts,
    NegState,
    Offer,
    Policy,
    Request,
    Requirement,
    decide,
)


@dataclass(frozen=True)
class Evaluation:
    variant_id: uuid.UUID
    decision: Decision
    product_name: str
    variant_name: str
    availability: str
    quantity: int = 1
    ask: str = "price"
    counter: Decimal | None = None
    state_changed: bool = False
    offer_names: dict[str, str] | None = None


def _offer_from_row(r: dict[str, Any]) -> Offer:
    cond = r["conditions"] or {}
    return Offer(id=str(r["id"]), name=r["name"], kind=r["kind"], value=r["value"], free_item=r["free_item"],
                 min_qty=cond.get("min_qty"), first_order_only=bool(cond.get("first_order")),
                 starts_at=r["starts_at"], ends_at=r["ends_at"], may_cross_floor=r["may_cross_floor"], active=r["active"],
                 variant_scoped=r["variant_id"] is not None)


class PricingService:
    def __init__(self, db: Database):
        self.db = db

    async def _load(self, business_id: uuid.UUID, variant_id: uuid.UUID) -> tuple[dict[str, Any], Decimal | None, list[Offer]]:
        """Policy + floor + offers through the pricing role. The floor never leaves this function's scope
        except inside the pure engine request."""
        async with self.db.pricing_tenant(business_id) as c:
            pol = await (await c.execute(
                """SELECT pp.*, v.availability, v.stock_qty, v.name AS variant_name, v.product_id
                   FROM pricing_policies pp JOIN product_variants v ON v.id = pp.variant_id WHERE pp.variant_id=%s""",
                (variant_id,))).fetchone()
            if pol is None:
                raise LookupError("variant has no pricing policy")
            fl = await (await c.execute("SELECT floor_price FROM price_floors WHERE variant_id=%s", (variant_id,))).fetchone()
            offers = await (await c.execute(
                "SELECT * FROM offers WHERE active AND (variant_id IS NULL OR variant_id=%s)", (variant_id,))).fetchall()
        return pol, (fl["floor_price"] if fl else None), [_offer_from_row(o) for o in offers]

    async def evaluate(self, business_id: uuid.UUID, conversation_id: uuid.UUID, variant_id: uuid.UUID, *,
                       ask: Ask = "price", counter: Decimal | None = None, quantity: int = 1,
                       advance_payment: bool = False, repeat_customer: bool = False, qualified: bool = True,
                       may_mention_offers: bool = True, now: datetime | None = None) -> Evaluation:
        """Pure evaluation: reads policy/floor/offers/state, writes NOTHING. The new negotiation state is
        saved with `save_in` inside the turn's commit transaction, so a superseded turn leaves no trace."""
        pol, floor, offers = await self._load(business_id, variant_id)
        async with self.db.tenant(business_id) as c:
            neg = await (await c.execute(
                """SELECT * FROM negotiations WHERE conversation_id=%s AND variant_id=%s AND status IN ('open','agreed')
                   ORDER BY created_at DESC LIMIT 1""", (conversation_id, variant_id))).fetchone()
            prod = await (await c.execute("SELECT name FROM products WHERE id=%s", (pol["product_id"],))).fetchone()
        if neg and neg["status"] == "agreed":
            state = NegState(step=neg["step"], customer_best=neg["customer_best"], status="agreed", agreed_price=neg["current_offer"]) \
                if ask == "price" else NegState()          # a new ask after agreement starts a fresh negotiation
        else:
            state = NegState(step=neg["step"], customer_best=neg["customer_best"], held_below_floor=neg["held_below_floor"]) if neg else NegState()
        policy = Policy(
            disclosure=pol["disclosure"], currency=pol["currency"], list_price=pol["list_price"], range_min=pol["range_min"],
            range_max=pol["range_max"], negotiable=pol["negotiable"], ai_may_negotiate=pol["ai_may_negotiate"],
            concession_steps=pol["concession_steps"], round_to=pol["round_to"],
            requires=tuple(Requirement(r["type"], r.get("min")) for r in (pol["concession_requires"] or [])))
        facts = Facts(now=now or datetime.now(UTC), quantity=max(1, quantity), advance_payment=advance_payment,
                      repeat_customer=repeat_customer, qualified=qualified, may_mention_offers=may_mention_offers,
                      availability=pol["availability"], stock_qty=pol["stock_qty"])
        d = decide(Request(policy, floor, tuple(offers), facts, state, ask, counter))
        return Evaluation(variant_id, d, prod["name"] if prod else "", pol["variant_name"], pol["availability"],
                          max(1, quantity), ask, counter, state_changed=(ask != "price" or d.state != state),
                          offer_names={o.id: o.name for o in offers})

    async def evaluate_and_save(self, business_id: uuid.UUID, conversation_id: uuid.UUID, variant_id: uuid.UUID,
                                **kw: Any) -> Evaluation:
        ev = await self.evaluate(business_id, conversation_id, variant_id, **kw)
        async with self.db.tenant(business_id) as c:
            await self.save_in(c, business_id, conversation_id, ev)
        return ev

    async def save_in(self, c: Any, business_id: uuid.UUID, conversation_id: uuid.UUID, ev: Evaluation) -> None:
        if not ev.state_changed:
            return
        d, variant_id, quantity, ask, counter = ev.decision, ev.variant_id, ev.quantity, ev.ask, ev.counter
        price = next((v.amount for v in d.values if v.kind == "price"), None)
        status = {"agreed": "agreed", "handed_off": "handed_off"}.get(d.state.status, "open")
        entry = {"at": datetime.now(UTC).isoformat(), "ask": ask, "counter": str(counter) if counter is not None else None,
                 "kind": d.kind, "price": str(price) if price is not None else None}
        # turns are serialized per conversation, so update-then-insert cannot race
        r = await c.execute(
            """UPDATE negotiations SET quantity=%s, current_offer=COALESCE(%s, current_offer), customer_best=%s, step=%s,
                      held_below_floor=%s, status=%s, history = history || %s::jsonb
               WHERE conversation_id=%s AND variant_id=%s AND status='open'""",
            (quantity, price, d.state.customer_best, d.state.step, d.state.held_below_floor, status, jsonb([entry]),
             conversation_id, variant_id))
        if r.rowcount == 0:
            await c.execute(
                """INSERT INTO negotiations (business_id, conversation_id, variant_id, quantity, current_offer, customer_best,
                                             step, held_below_floor, status, history)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (business_id, conversation_id, variant_id, quantity, price, d.state.customer_best, d.state.step,
                 d.state.held_below_floor, status, jsonb([entry])))
