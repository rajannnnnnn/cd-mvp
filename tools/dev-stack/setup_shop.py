"""Development helper: creates ONE real business in the dev database through the product's own functions, with a WhatsApp number that
points at the independent Meta simulator, and a small catalog. Run it with the product's environment loaded:

    cd backend && python ../tools/dev-stack/setup_shop.py

It is not part of the product package and nothing in the product imports it. Idempotent by shop name: it stops if the shop already exists.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from salesai.config import get_settings
from salesai.db import jsonb
from salesai.modules.catalog import PolicyIn, ProductIn, VariantIn, repo
from salesai.modules.tenants import add_cloud_number, create_business
from salesai.runtime import Runtime

NAME = "Sharma Sarees"
OWNER_PHONE = "+919800009999"
SIM = {"phone_number_id": "sim-pn-1", "waba_id": "sim-waba-1", "display_phone": "+919700000001", "access_token": "SIMTOKEN-business-1"}

PROFILE = {
    "address": "Shop 14, Laxmi Road, Pune 411002",
    "hours": {d: "10:30-20:30" for d in ("mon", "tue", "wed", "thu", "fri")} | {"sat": "10:30-21:00", "sun": "closed"},
    "delivery": "Free home delivery in Pune within 2 days. Courier across India in 4-6 days.",
    "returns": "Exchange within 7 days with the bill.",
    "payment_modes": ["UPI", "Cash", "Card"],
    "facts": ["Fall and pico is free on every saree.", "All Banarasi sarees are handwoven with a silk-mark certificate."],
}

# conversation pacing: keep the real end-of-turn logic, but short human-like delays so a tester is not left waiting on purpose
TIMING = {"read_delay": {"mu": 6.0, "sigma": 0.2, "min_ms": 300, "max_ms": 900},
          "typing_ms_per_char": {"mu": 3.0, "sigma": 0.3, "min_ms": 5, "max_ms": 25},
          "part_gap": {"mu": 6.5, "sigma": 0.2, "min_ms": 400, "max_ms": 1500}, "max_total_delay_ms": 4000, "slower_multiplier": 2.5}

PRODUCTS = [
    dict(name="Banarasi Silk Saree", price="8500", floor="7731", steps=3, description="Handwoven pure silk with zari border", category="sarees",
         aliases=["banarasi", "silk saree", "sari"], availability="limited", stock=3, round_to="1"),
    dict(name="Cotton Saree", price="1450", floor="1233", steps=2, description="Soft everyday cotton", category="sarees", aliases=["cotton"], round_to="1"),
    dict(name="Bridal Lehenga", price=None, floor=None, disclosure="on_request", description="Custom bridal lehenga, made to measure",
         category="lehengas", aliases=["lehenga", "bridal"], availability="made_to_order"),
    dict(name="Kurta Set", price=None, floor=None, disclosure="range", rng=("1200", "2400"), description="Cotton kurta sets", category="kurtas", aliases=["kurta"]),
]


async def main() -> None:
    rt = await Runtime.create(get_settings())
    try:
        async with rt.db.system_tx() as s:
            if await (await s.execute("SELECT 1 FROM businesses WHERE name=%s", (NAME,))).fetchone():
                print(f"{NAME} already exists; nothing to do")
                return
        cb = await create_business(rt.db, rt.settings.master_key_bytes, name=NAME, owner_phone=OWNER_PHONE, owner_name="Sharma ji",
                                   ai_enabled=True, profile=PROFILE)
        bid = cb.business_id
        await add_cloud_number(rt.db, rt.vault, bid, coexistence=True, verified_name=NAME, **SIM)
        async with rt.db.tenant(bid) as c:
            await c.execute("UPDATE businesses SET timing_params=%s WHERE id=%s", (jsonb(TIMING), bid))
            for p in PRODUCTS:
                fl = p.get("floor")
                pol = PolicyIn(disclosure=p.get("disclosure", "fixed"), list_price=Decimal(p["price"]) if p.get("price") else None,
                               range_min=Decimal(p["rng"][0]) if p.get("rng") else None, range_max=Decimal(p["rng"][1]) if p.get("rng") else None,
                               negotiable=fl is not None, ai_may_negotiate=fl is not None, concession_steps=p.get("steps", 0) if fl else 0,
                               floor_price=Decimal(fl) if fl else None, concession_requires=[], round_to=Decimal(p.get("round_to", "10")))
                await repo.create_product(c, bid, ProductIn(name=p["name"], description=p.get("description"), category=p.get("category"),
                                                            attributes={"aliases": p.get("aliases", [])},
                                                            variants=[VariantIn(name="default", availability=p.get("availability", "in_stock"),
                                                                                stock_qty=p.get("stock"), policy=pol)]))
        print(f"created {NAME}: business {bid}, number {SIM['display_phone']} (phone_number_id {SIM['phone_number_id']}), {len(PRODUCTS)} products")
    finally:
        await rt.close()


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
