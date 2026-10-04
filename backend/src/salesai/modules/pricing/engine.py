"""Pricing engine — the ONLY component that turns owner pricing rules into numbers a customer may hear.

Deterministic and free of I/O (no database, no network, no clock: `now` is an input), so it can be
tested exhaustively. Invariants it enforces:
  INV-2  returns the exact set of values it issued; the reply check rejects any other number.
  INV-3  never yields a price below the floor, except via an offer the owner marked may_cross_floor.
  INV-4  never yields any number for an on_request item.
Negotiation rules (Technical Design: Pricing engine): negotiates only when negotiable AND
ai_may_negotiate; concedes in steps; can require something in return; never offers less than the
customer already offered; below-floor insistence is held once with no new number, then handed off.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any, Literal

CENT = Decimal("0.01")
Disclosure = Literal["fixed", "range", "starts_from", "after_qualifying", "on_request"]
OfferKind = Literal["percent", "flat", "bundle", "free_item"]
ReqType = Literal["quantity", "advance_payment", "repeat_customer"]
Ask = Literal["price", "discount", "counter"]
DecisionKind = Literal["quote", "firm", "concede", "accept", "hold", "needs_concession", "ask_qualify", "handoff"]


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT)


def ceil_to(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_CEILING) * step


@dataclass(frozen=True)
class Requirement:
    type: ReqType
    min: int | None = None


@dataclass(frozen=True)
class Policy:
    disclosure: Disclosure
    currency: str = "INR"
    list_price: Decimal | None = None
    range_min: Decimal | None = None
    range_max: Decimal | None = None
    negotiable: bool = False
    ai_may_negotiate: bool = False
    concession_steps: int = 0
    requires: tuple[Requirement, ...] = ()
    round_to: Decimal = Decimal("1")


@dataclass(frozen=True)
class Offer:
    id: str
    name: str
    kind: OfferKind
    value: Decimal | None = None
    free_item: str | None = None
    min_qty: int | None = None
    first_order_only: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    may_cross_floor: bool = False
    active: bool = True
    variant_scoped: bool = False   # informational; scoping is done by the loader


@dataclass(frozen=True)
class Facts:
    now: datetime
    quantity: int = 1
    advance_payment: bool = False
    repeat_customer: bool = False
    qualified: bool = True          # qualification data captured (for after_qualifying)
    may_mention_offers: bool = True
    availability: str = "in_stock"
    stock_qty: int | None = None


@dataclass(frozen=True)
class NegState:
    step: int = 0                                   # concessions granted so far (0 = at list)
    customer_best: Decimal | None = None            # highest the customer has offered
    held_below_floor: bool = False                  # held once already
    status: Literal["open", "agreed", "handed_off"] = "open"
    agreed_price: Decimal | None = None             # price the customer accepted (restated on request)


@dataclass(frozen=True)
class Request:
    policy: Policy
    floor: Decimal | None
    offers: tuple[Offer, ...]
    facts: Facts
    state: NegState = NegState()
    ask: Ask = "price"
    counter: Decimal | None = None


@dataclass(frozen=True)
class Value:
    """A number the customer-facing reply may contain."""
    kind: Literal["price", "list_price", "range_min", "range_max", "total", "discount_percent",
                  "quantity", "stock"]
    amount: Decimal
    currency: str = "INR"

    def display(self) -> str:
        return f"{self.amount.normalize():f}" if self.amount == self.amount.to_integral() else f"{self.amount}"


@dataclass(frozen=True)
class Urgency:
    """Real facts that justify urgency (FR-SL-6). Anything else is invented urgency (CR-6)."""
    kind: Literal["offer_ends", "low_stock"]
    detail: str


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    values: tuple[Value, ...] = ()
    semantics: Literal["exact", "range", "from", "none"] = "none"
    requires: tuple[str, ...] = ()                    # unmet concession conditions
    handoff_reason: str | None = None                 # a handoffs.reason value
    note: str = ""
    applied_offers: tuple[str, ...] = ()
    free_items: tuple[str, ...] = ()
    can_concede_more: bool = False
    state: NegState = NegState()
    via_floor_crossing_offer: bool = False
    urgency: tuple[Urgency, ...] = ()

    def issued(self) -> frozenset[tuple[str, str]]:
        return frozenset((v.kind, f"{v.amount:f}") for v in self.values)

    def issued_amounts(self) -> frozenset[Decimal]:
        return frozenset(v.amount for v in self.values)

    def for_llm(self) -> dict[str, Any]:
        """What the model may see. No floor, no schedule, no internal state beyond intent."""
        return {
            "kind": self.kind,
            "semantics": self.semantics,
            "values": [{"kind": v.kind, "amount": f"{v.amount:f}", "currency": v.currency} for v in self.values],
            "unmet_conditions": list(self.requires),
            "applied_offers": list(self.applied_offers),
            "free_items": list(self.free_items),
            "final_offer": not self.can_concede_more,
            "handoff": self.handoff_reason is not None,
            "real_urgency": [{"kind": u.kind, "detail": u.detail} for u in self.urgency],
        }


# ------------------------------------------------------------------------------- schedule
def concession_levels(list_price: Decimal, floor: Decimal, steps: int, round_to: Decimal) -> list[Decimal]:
    """Strictly decreasing price levels from list down to floor (the last level IS the floor).
    Even spacing, rounded UP to the business's rounding rule so a level never undercuts the floor."""
    if steps <= 0 or floor >= list_price:
        return []
    levels: list[Decimal] = []
    prev = list_price
    for k in range(1, steps + 1):
        if k == steps:
            lvl = floor
        else:
            raw = list_price - (list_price - floor) * k / steps
            lvl = max(floor, min(prev, ceil_to(raw, round_to)))
        lvl = q2(lvl)
        if lvl < prev:
            levels.append(lvl)
            prev = lvl
    return levels


def price_at(list_price: Decimal, levels: list[Decimal], step: int) -> Decimal:
    if step <= 0 or not levels:
        return q2(list_price)
    return levels[min(step, len(levels)) - 1]


# ------------------------------------------------------------------------------- offers
def offer_applies(o: Offer, f: Facts) -> bool:
    if not o.active:
        return False
    if o.starts_at and f.now < o.starts_at:
        return False
    if o.ends_at and f.now > o.ends_at:
        return False
    if o.min_qty and f.quantity < o.min_qty:
        return False
    return not (o.first_order_only and f.repeat_customer)


def offer_price(o: Offer, base: Decimal) -> Decimal | None:
    if o.kind == "percent" and o.value is not None:
        return q2(base * (Decimal(100) - o.value) / Decimal(100))
    if o.kind == "flat" and o.value is not None:
        return q2(max(Decimal(0), base - o.value))
    if o.kind == "bundle" and o.value is not None:
        return q2(o.value)
    return None   # free_item has no price effect


def best_offer(req: Request, base: Decimal) -> tuple[Decimal, Offer | None, bool]:
    """Lowest price reachable via an applicable offer, never below floor unless the offer may cross it.
    Returns (price, offer, crossed_floor)."""
    best: tuple[Decimal, Offer | None, bool] = (base, None, False)
    for o in req.offers:
        if not offer_applies(o, req.facts):
            continue
        p = offer_price(o, base)
        if p is None:
            continue
        crossed = False
        if req.floor is not None and p < req.floor:
            if o.may_cross_floor:
                crossed = True
            else:
                p = q2(req.floor)
        if p < best[0]:
            best = (p, o, crossed)
    return best


def free_items(req: Request) -> tuple[str, ...]:
    return tuple(o.free_item for o in req.offers if o.kind == "free_item" and o.free_item and offer_applies(o, req.facts))


# ------------------------------------------------------------------------------- decision
def decide(req: Request) -> Decision:
    """Entry point. Pure function of its input."""
    if req.policy.disclosure == "on_request":              # INV-4: never a number
        return _handoff("on_request_price", req.state, "price is on request")
    if req.ask == "price" and req.state.status == "agreed" and req.state.agreed_price is not None \
            and (req.floor is None or req.state.agreed_price >= req.floor) and req.policy.list_price is not None:
        # restate the price the customer already accepted, issued afresh by the engine this turn
        p = q2(req.state.agreed_price)
        d: Decision = Decision(kind="quote", semantics="exact", state=req.state, values=_vals(req, p, list_price=req.policy.list_price))
        return _with_urgency(req, d)
    d = _quote(req) if req.ask == "price" else _negotiate(req)
    return _with_urgency(req, d)


def _handoff(reason: str, state: NegState, note: str) -> Decision:
    return Decision(kind="handoff", handoff_reason=reason, note=note, state=replace(state, status="handed_off"))


def _with_urgency(req: Request, d: Decision) -> Decision:
    """Attach only REAL urgency facts: an applied offer's end date, genuinely limited stock (FR-SL-6)."""
    if not d.values or d.kind not in ("quote", "firm"):    # urgency belongs to an initial quote, not to every concession
        return d
    items: list[Urgency] = []
    for o in req.offers:
        if o.id in d.applied_offers and o.ends_at:
            items.append(Urgency("offer_ends", o.ends_at.isoformat()))
    extra: tuple[Value, ...] = ()
    if req.facts.availability == "limited" and req.facts.stock_qty is not None and req.facts.stock_qty > 0:
        items.append(Urgency("low_stock", f"{req.facts.stock_qty} left"))
        extra = (Value("stock", Decimal(req.facts.stock_qty), req.policy.currency),)
    return replace(d, urgency=tuple(items), values=d.values + extra)


def _unmet(req: Request) -> tuple[str, ...]:
    out = []
    for r in req.policy.requires:
        if r.type == "quantity" and req.facts.quantity < (r.min or 2):
            out.append(f"quantity>={r.min or 2}")
        elif r.type == "advance_payment" and not req.facts.advance_payment:
            out.append("advance_payment")
        elif r.type == "repeat_customer" and not req.facts.repeat_customer:
            out.append("repeat_customer")
    return tuple(out)


def _vals(req: Request, price: Decimal, *, list_price: Decimal | None = None, pct: Decimal | None = None) -> tuple[Value, ...]:
    cur = req.policy.currency
    out = [Value("price", price, cur)]
    if list_price is not None and list_price != price:
        out.append(Value("list_price", q2(list_price), cur))
    if pct is not None:
        out.append(Value("discount_percent", pct, cur))
    if req.facts.quantity > 1:
        out.append(Value("quantity", Decimal(req.facts.quantity), cur))
        out.append(Value("total", q2(price * req.facts.quantity), cur))
    return tuple(out)


def _levels(req: Request) -> list[Decimal]:
    p = req.policy
    if not (p.negotiable and p.ai_may_negotiate) or req.floor is None or p.list_price is None:
        return []
    return concession_levels(p.list_price, req.floor, p.concession_steps, p.round_to)


def _current_price(req: Request, levels: list[Decimal]) -> tuple[Decimal, Offer | None, bool]:
    """What we are asking now: the negotiated level or an applicable offer price, whichever is lower."""
    assert req.policy.list_price is not None
    negotiated = price_at(req.policy.list_price, levels, req.state.step)
    if not req.facts.may_mention_offers and req.ask == "price":
        return negotiated, None, False
    return best_offer(req, negotiated)


def _quote(req: Request) -> Decision:
    """A plain price answer, per disclosure mode (FR-CF-3)."""
    p, f, st, cur = req.policy, req.facts, req.state, req.policy.currency
    if p.disclosure == "range":
        assert p.range_min is not None and p.range_max is not None  # enforced by DB CHECK
        return Decision(kind="quote", semantics="range", state=st, free_items=free_items(req),
                        values=(Value("range_min", q2(p.range_min), cur), Value("range_max", q2(p.range_max), cur)))
    if p.disclosure == "starts_from":
        assert p.list_price is not None
        return Decision(kind="quote", semantics="from", state=st, free_items=free_items(req),
                        values=(Value("price", q2(p.list_price), cur),))
    if p.disclosure == "after_qualifying" and not f.qualified:
        return Decision(kind="ask_qualify", note="collect needs/quantity before quoting", state=st)
    if p.list_price is None:
        if p.range_min is not None and p.range_max is not None:
            return Decision(kind="quote", semantics="range", state=st,
                            values=(Value("range_min", q2(p.range_min), cur), Value("range_max", q2(p.range_max), cur)))
        return _handoff("on_request_price", st, "no price configured")
    return _exact_quote(req)


def _exact_quote(req: Request, kind: DecisionKind = "quote", note: str = "") -> Decision:
    p = req.policy
    assert p.list_price is not None
    levels = _levels(req)
    price, offer, crossed = _current_price(req, levels)
    pct = offer.value if (offer and offer.kind == "percent") else None
    can_more = req.state.step < len(levels) and not crossed and price > (req.floor or Decimal(0))
    return Decision(
        kind=kind, semantics="exact", state=req.state, note=note,
        values=_vals(req, price, list_price=p.list_price if price != p.list_price else None, pct=pct),
        applied_offers=(offer.id,) if offer else (), free_items=free_items(req),
        can_concede_more=can_more, via_floor_crossing_offer=crossed)


def _negotiate(req: Request) -> Decision:
    p, f, st = req.policy, req.facts, req.state

    if p.disclosure == "after_qualifying" and not f.qualified:
        return Decision(kind="ask_qualify", note="collect needs/quantity before quoting", state=st)
    if p.disclosure in ("range", "starts_from") or p.list_price is None:
        return _quote(req) if not p.negotiable else _handoff("other", st, "negotiation_not_supported_for_disclosure")
    if not p.negotiable:
        return _exact_quote(req, "firm", "price is fixed")
    if not p.ai_may_negotiate or req.floor is None:        # owner negotiates personally
        return _handoff("other", st, "negotiation_requires_owner")

    levels = _levels(req)
    price, offer, crossed = _current_price(req, levels)
    counter = req.counter if req.ask == "counter" else None
    applied = (offer.id,) if offer else ()

    best = st.customer_best
    if counter is not None:
        best = counter if best is None else max(best, counter)
    st = replace(st, customer_best=best)

    # 1. the customer offers at or above what we already ask: that is a deal at OUR price
    if counter is not None and counter >= price:
        return Decision(kind="accept", semantics="exact", values=_vals(req, q2(price)),
                        state=replace(st, status="agreed", agreed_price=q2(price)), applied_offers=applied,
                        via_floor_crossing_offer=crossed, note="customer accepted our price")

    # 2. is there a concession left to give? (an offer already below the next level means no)
    nxt: Decimal | None = None
    if not crossed and st.step < len(levels) and levels[st.step] < price:
        nxt = levels[st.step]

    if nxt is not None:
        unmet = _unmet(req)
        if unmet:                                           # ask for something in return first
            return Decision(kind="needs_concession", requires=unmet, semantics="exact", state=st,
                            values=_vals(req, price), applied_offers=applied, can_concede_more=True)
        if counter is not None and counter >= nxt:          # within reach: take the customer's number
            return Decision(kind="accept", semantics="exact", values=_vals(req, q2(counter)),
                            state=replace(st, status="agreed", agreed_price=q2(counter)), applied_offers=applied,
                            note="accepted customer's offer")
        new_price = nxt
        if best is not None and new_price < best:           # never offer less than the customer offered
            new_price = q2(best)
        new_step = st.step + 1
        return Decision(kind="concede", semantics="exact",
                        values=_vals(req, new_price, list_price=p.list_price),
                        state=replace(st, step=new_step, held_below_floor=False),
                        can_concede_more=new_step < len(levels), note="conceded one step")

    # 3. nothing left to give: hold once with no new number, then hand off
    if st.held_below_floor:
        return _handoff("below_floor", st, "customer insists below the final price")
    return Decision(kind="hold", semantics="exact", values=_vals(req, q2(price)), note="cannot go lower",
                    state=replace(st, held_below_floor=True), applied_offers=applied,
                    via_floor_crossing_offer=crossed)
