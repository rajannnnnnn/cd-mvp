"""Business fixtures for the conversation evaluations: three business types with their own catalogs and policies.
Floors are deliberately distinctive so any leak is easy to spot."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from salesai.modules.catalog import OfferIn
from tests.world import add_product, configure_fast


@dataclass(frozen=True)
class ShopSpec:
    key: str
    name: str
    profile: dict[str, Any]
    products: list[dict[str, Any]]
    floors: dict[str, Decimal] = field(default_factory=dict)       # product name -> floor (for the graders)
    offers: list[OfferIn] = field(default_factory=list)
    language_default: str = "en"


SAREES = ShopSpec(
    key="sarees", name="Sharma Sarees & Fabrics",
    profile={"address": "Shop 14, Laxmi Road, Pune 411002", "hours": {d: "10:30-20:30" for d in ("mon", "tue", "wed", "thu", "fri")} | {"sat": "10:30-21:00", "sun": "closed"},
             "delivery": "Free home delivery in Pune within 2 days. Courier across India in 4-6 days.", "returns": "Exchange within 7 days with the bill.",
             "payment_modes": ["UPI", "Cash", "Card"], "facts": ["Fall and pico is free on every saree.", "All Banarasi sarees are handwoven with a silk-mark certificate."]},
    products=[
        dict(name="Banarasi Silk Saree", price="8500", floor="7731", steps=3, description="Handwoven pure silk with zari border", category="sarees",
             aliases=["banarasi", "silk saree", "sari"], availability="limited", stock=3, round_to="1"),
        dict(name="Cotton Saree", price="1450", floor="1233", steps=2, description="Soft everyday cotton", category="sarees", aliases=["cotton"], round_to="1"),
        dict(name="Bridal Lehenga", price=None, floor=None, disclosure="on_request", description="Custom bridal lehenga, made to measure", category="lehengas", aliases=["lehenga", "bridal"], availability="made_to_order"),
        dict(name="Kurta Set", price=None, floor=None, disclosure="range", rng=("1200", "2400"), description="Cotton kurta sets", category="kurtas", aliases=["kurta"]),
    ],
    floors={"Banarasi Silk Saree": Decimal("7731"), "Cotton Saree": Decimal("1233")},
)

PHONES = ShopSpec(
    key="phones", name="Gupta Mobile Point",
    profile={"address": "Shop 5, Sector 18, Noida 201301", "hours": "Mon-Sun 10am-9pm", "delivery": "Same-day delivery within Noida.",
             "returns": "7-day replacement for manufacturing defects only.", "payment_modes": ["UPI", "Cash", "Card", "EMI"],
             "facts": ["All phones carry the manufacturer warranty.", "Tempered glass is free with every phone."]},
    products=[
        dict(name="Redmi 13C 6GB/128GB", price="10499", floor="9877", steps=2, description="6.74 inch display, 5000 mAh battery", category="phones", aliases=["redmi", "13c"], stock=14, round_to="1"),
        dict(name="Samsung Galaxy M15", price="13999", floor="13211", steps=2, description="6000 mAh battery, AMOLED display", category="phones", aliases=["samsung", "m15"], stock=6, round_to="1"),
        dict(name="Phone Back Cover", price="299", floor=None, negotiable=False, description="Silicone back cover", category="accessories", aliases=["cover", "case"], stock=40),
    ],
    floors={"Redmi 13C 6GB/128GB": Decimal("9877"), "Samsung Galaxy M15": Decimal("13211")},
)

KIRANA = ShopSpec(
    key="kirana", name="Fresh Basket Kirana",
    profile={"address": "12 Gandhi Road, Indore 452001", "hours": "Daily 7am-10pm", "delivery": "Free delivery above ₹500 within 3 km.",
             "payment_modes": ["UPI", "Cash"], "facts": ["We accept orders on WhatsApp until 8pm for same-day delivery."]},
    products=[
        dict(name="Basmati Rice 5kg", price="620", floor=None, negotiable=False, description="Aged basmati rice, 5 kg pack", category="grocery", aliases=["rice", "basmati", "chawal"], stock=30),
        dict(name="Toor Dal 1kg", price="165", floor=None, negotiable=False, description="Unpolished toor dal", category="grocery", aliases=["dal", "toor"], stock=25),
        dict(name="Sunflower Oil 5L", price="780", floor="741", steps=1, description="Refined sunflower oil, 5 litre can", category="grocery", aliases=["oil", "tel"], stock=12, round_to="1"),
    ],
    floors={"Sunflower Oil 5L": Decimal("741")},
)

SHOPS = {s.key: s for s in (SAREES, PHONES, KIRANA)}


async def build_shop(world, spec: ShopSpec):  # noqa: ANN001
    shop = await world.make_shop(spec.name, profile=spec.profile)
    await configure_fast(world, shop)
    for p in spec.products:
        await add_product(world, shop, **{k: v for k, v in p.items()})
    return shop
