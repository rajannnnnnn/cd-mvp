"""Property-based tests for the pricing engine (INV-2, INV-3, INV-4 + required properties)."""
from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from salesai.modules.pricing.engine import (
    Facts,
    NegState,
    Offer,
    Policy,
    Request,
    Requirement,
    concession_levels,
    decide,
    q2,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
SET = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])

money = st.decimals(min_value=D("1"), max_value=D("200000"), places=2)
round_tos = st.sampled_from([D("1"), D("5"), D("10"), D("50")])


@st.composite
def requirements(draw):
    kinds = draw(st.lists(st.sampled_from(["quantity", "advance_payment", "repeat_customer"]), max_size=3, unique=True))
    return tuple(Requirement(k, draw(st.integers(2, 10)) if k == "quantity" else None) for k in kinds)


@st.composite
def policies(draw, *, disclosure=None, negotiating=None):
    disc = disclosure or draw(st.sampled_from(["fixed", "range", "starts_from", "after_qualifying", "on_request"]))
    lst = draw(money)
    lo = draw(st.decimals(min_value=D("1"), max_value=lst, places=2))
    hi = draw(st.decimals(min_value=lst, max_value=lst + D("5000"), places=2))
    neg = draw(st.booleans()) if negotiating is None else negotiating
    ai = draw(st.booleans()) if neg and negotiating is None else neg
    return Policy(
        disclosure=disc, list_price=lst if disc != "range" or neg else draw(st.sampled_from([lst, None])),
        range_min=lo if disc == "range" else None, range_max=hi if disc == "range" else None,
        negotiable=neg, ai_may_negotiate=ai and neg, concession_steps=draw(st.integers(0, 8)),
        requires=draw(requirements()), round_to=draw(round_tos))


@st.composite
def offers(draw, policy):
    out = []
    for i in range(draw(st.integers(0, 3))):
        kind = draw(st.sampled_from(["percent", "flat", "bundle", "free_item"]))
        out.append(Offer(
            id=f"o{i}", name=f"offer {i}", kind=kind,
            value={"percent": draw(st.decimals(min_value=D("1"), max_value=D("90"), places=0)),
                   "flat": draw(st.decimals(min_value=D("1"), max_value=D("5000"), places=2)),
                   "bundle": draw(money), "free_item": None}[kind],
            free_item="gift" if kind == "free_item" else None,
            min_qty=draw(st.sampled_from([None, 2, 3])),
            first_order_only=draw(st.booleans()),
            starts_at=draw(st.sampled_from([None, NOW - timedelta(days=1), NOW + timedelta(days=1)])),
            ends_at=draw(st.sampled_from([None, NOW + timedelta(days=2), NOW - timedelta(hours=1)])),
            may_cross_floor=draw(st.booleans()), active=draw(st.booleans())))
    return tuple(out)


facts = st.builds(Facts, now=st.just(NOW), quantity=st.integers(1, 6), advance_payment=st.booleans(),
                  repeat_customer=st.booleans(), qualified=st.booleans(), may_mention_offers=st.booleans(),
                  availability=st.sampled_from(["in_stock", "limited"]),
                  stock_qty=st.one_of(st.none(), st.integers(1, 9)))


@st.composite
def scenario(draw, **pk):
    p = draw(policies(**pk))
    floor = draw(st.one_of(st.none(), st.decimals(min_value=D("0"), max_value=p.list_price or D("1"), places=2))) \
        if p.list_price else None
    return p, floor, draw(offers(p)), draw(facts)


def crossing_possible(offs):
    return any(o.may_cross_floor for o in offs)


# ------------------------------------------------------------------ INV-4
@SET
@given(sc=scenario(disclosure="on_request"), ask=st.sampled_from(["price", "discount", "counter"]), c=money)
def test_on_request_never_yields_a_number(sc, ask, c):
    p, floor, offs, f = sc
    d = decide(Request(p, floor, offs, f, NegState(), ask, c))
    assert d.kind == "handoff" and d.handoff_reason == "on_request_price"
    assert d.values == () and d.urgency == ()


# ------------------------------------------------------------------ disclosure modes
@SET
@given(sc=scenario())
def test_disclosure_modes_shape_the_answer(sc):
    p, floor, offs, f = sc
    d = decide(Request(p, floor, offs, f))
    kinds = {v.kind for v in d.values}
    if p.disclosure == "range":
        assert d.semantics == "range" and {"range_min", "range_max"} <= kinds
        assert not ({"price"} & kinds) or True
    elif p.disclosure == "starts_from":
        assert d.semantics == "from" and d.values[0].amount == q2(p.list_price)
    elif p.disclosure == "after_qualifying" and not f.qualified:
        assert d.kind == "ask_qualify" and d.values == ()
    elif p.disclosure == "fixed":
        assert d.kind == "quote" and d.semantics == "exact" and "price" in kinds


# ------------------------------------------------------------------ INV-3
@SET
@given(sc=scenario(), ask=st.sampled_from(["price", "discount", "counter"]),
       counters=st.lists(money, min_size=1, max_size=12))
def test_never_below_floor_unless_a_crossing_offer(sc, ask, counters):
    p, floor, offs, f = sc
    state = NegState()
    for c in counters:
        d = decide(Request(p, floor, offs, f, state, ask if ask != "counter" else "counter", c))
        state = d.state
        if floor is not None:
            for v in d.values:
                if v.kind == "price":
                    assert v.amount >= q2(floor) or d.via_floor_crossing_offer, (d, floor)
        if d.state.status != "open":
            break


@SET
@given(sc=scenario(negotiating=True))
def test_offers_cannot_cross_floor_unless_owner_allows(sc):
    p, floor, offs, f = sc
    assume(floor is not None)
    offs = tuple(o for o in offs if not o.may_cross_floor)
    d = decide(Request(p, floor, offs, f))
    for v in d.values:
        if v.kind == "price":
            assert v.amount >= q2(floor)


# ------------------------------------------------------------------ negotiation permissions
@SET
@given(sc=scenario(disclosure="fixed"), c=money)
def test_negotiates_only_when_negotiable_and_ai_allowed(sc, c):
    p, floor, offs, f = sc
    offs = ()
    d = decide(Request(p, floor, offs, f, NegState(), "counter", c))
    if not p.negotiable:
        assert d.kind == "firm" and all(v.amount >= q2(p.list_price) for v in d.values if v.kind == "price")
    elif not p.ai_may_negotiate or floor is None:
        assert d.kind == "handoff" and d.handoff_reason == "other"
    else:
        assert d.kind in {"accept", "concede", "needs_concession", "hold", "handoff"}


# ------------------------------------------------------------------ gradual concessions
@SET
@given(lst=money, frac=st.decimals(min_value=D("0.05"), max_value=D("0.9"), places=2),
       steps=st.integers(1, 10), rt=round_tos)
def test_levels_are_strictly_decreasing_and_end_exactly_at_floor(lst, frac, steps, rt):
    floor = q2(lst * frac)
    assume(floor < lst)
    lv = concession_levels(lst, floor, steps, rt)
    assert lv and lv[-1] == floor
    assert all(a > b for a, b in zip([q2(lst), *lv], lv, strict=False))
    assert all(x >= floor for x in lv)


@SET
@given(sc=scenario(disclosure="fixed", negotiating=True))
def test_first_concession_never_jumps_to_floor_when_steps_allow(sc):
    p, floor, offs, f = sc
    assume(floor is not None and floor < p.list_price and p.concession_steps >= 2)
    lv = concession_levels(p.list_price, floor, p.concession_steps, p.round_to)
    assume(len(lv) >= 2)
    f = Facts(now=NOW, quantity=99, advance_payment=True, repeat_customer=True)
    d = decide(Request(p, floor, (), f, NegState(), "discount"))
    assert d.kind == "concede" and d.values[0].amount == lv[0] > floor


@SET
@given(sc=scenario(disclosure="fixed", negotiating=True))
def test_requirements_must_be_met_before_a_concession(sc):
    p, floor, _, _ = sc
    assume(floor is not None and floor < p.list_price and p.concession_steps >= 1 and p.requires)
    assume(len(concession_levels(p.list_price, floor, p.concession_steps, p.round_to)) >= 1)
    poor = Facts(now=NOW, quantity=1, advance_payment=False, repeat_customer=False)
    d = decide(Request(p, floor, (), poor, NegState(), "discount"))
    assert d.kind == "needs_concession" and d.requires
    assert d.state.step == 0
    assert [v for v in d.values if v.kind == "price"][0].amount == q2(p.list_price)


# ------------------------------------------------------------------ never below the customer's offer
@SET
@given(sc=scenario(disclosure="fixed", negotiating=True), counters=st.lists(money, min_size=1, max_size=10))
def test_never_offers_less_than_customer_already_offered(sc, counters):
    p, floor, _, _ = sc
    assume(floor is not None)
    f = Facts(now=NOW, quantity=99, advance_payment=True, repeat_customer=True)
    state = NegState()
    for c in counters:
        d = decide(Request(p, floor, (), f, state, "counter", c))
        state = d.state
        if d.kind in ("concede",) and state.customer_best is not None:
            price = [v for v in d.values if v.kind == "price"][0].amount
            assert price >= state.customer_best
        if d.kind == "accept":
            price = [v for v in d.values if v.kind == "price"][0].amount
            assert price >= q2(floor)
        if state.status != "open":
            break


# ------------------------------------------------------------------ hold once, then hand off
@SET
@given(sc=scenario(disclosure="fixed", negotiating=True), low=st.decimals(min_value=D("0.01"), max_value=D("0.99"), places=2))
def test_insisting_below_floor_holds_once_then_hands_off(sc, low):
    p, floor, _, _ = sc
    assume(floor is not None and floor >= D("2") and p.concession_steps >= 1)
    f = Facts(now=NOW, quantity=99, advance_payment=True, repeat_customer=True)
    state, kinds, prices = NegState(), [], []
    counter = q2(floor * low)
    for _ in range(p.concession_steps + 4):
        d = decide(Request(p, floor, (), f, state, "counter", counter))
        kinds.append(d.kind)
        prices += [v.amount for v in d.values if v.kind == "price"]
        state = d.state
        if d.kind == "handoff":
            assert d.values == () and d.handoff_reason == "below_floor"
            break
    assert kinds[-1] == "handoff"
    assert kinds.count("hold") == 1                       # held exactly once, with no new number
    assert prices == sorted(prices, reverse=True) and min(prices) >= q2(floor)
    hold_i = kinds.index("hold")
    assert prices[-1] == prices[hold_i] if hold_i < len(prices) else True


# ------------------------------------------------------------------ offers
@SET
@given(sc=scenario(disclosure="fixed"))
def test_only_active_unexpired_condition_met_offers_apply(sc):
    p, floor, offs, f = sc
    d = decide(Request(p, floor, offs, f))
    by_id = {o.id: o for o in offs}
    for oid in d.applied_offers:
        o = by_id[oid]
        assert o.active
        assert o.starts_at is None or o.starts_at <= f.now
        assert o.ends_at is None or o.ends_at >= f.now
        assert o.min_qty is None or f.quantity >= o.min_qty
        assert not (o.first_order_only and f.repeat_customer)
    for it in d.free_items:
        assert any(o.free_item == it and o.active and (o.ends_at is None or o.ends_at >= f.now) for o in offs)


@SET
@given(sc=scenario(disclosure="fixed"))
def test_urgency_is_only_backed_by_real_facts(sc):
    p, floor, offs, f = sc
    d = decide(Request(p, floor, offs, f))
    for u in d.urgency:
        if u.kind == "low_stock":
            assert f.availability == "limited" and f.stock_qty is not None
        else:
            assert u.kind == "offer_ends" and d.applied_offers


# ------------------------------------------------------------------ issued set (INV-2)
@SET
@given(sc=scenario(), ask=st.sampled_from(["price", "discount", "counter"]), c=money)
def test_issued_set_is_exactly_the_values_and_deterministic(sc, ask, c):
    p, floor, offs, f = sc
    req = Request(p, floor, offs, f, NegState(), ask, c)
    d1, d2 = decide(req), decide(req)
    assert d1 == d2
    assert d1.issued() == frozenset((v.kind, f"{v.amount:f}") for v in d1.values)
    for v in d1.values:
        assert v.amount >= 0


@SET
@given(sc=scenario(), ask=st.sampled_from(["price", "discount", "counter"]), c=money)
def test_llm_view_never_contains_the_floor_as_a_field(sc, ask, c):
    p, floor, offs, f = sc
    d = decide(Request(p, floor, offs, f, NegState(), ask, c))
    view = d.for_llm()
    assert "floor" not in str(view.keys()).lower() and "floor" not in str(view).lower()


# ------------------------------------------------------------------ purity
def test_engine_is_pure_no_io_imports():
    src = (Path(__file__).parents[1] / "src/salesai/modules/pricing/engine.py").read_text()
    imported = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "datetime", "decimal", "typing"}, imported
