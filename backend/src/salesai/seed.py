"""Demo data for development and co-founder demos: python -m salesai.seed [--conversations] [--spread-days N]

Creates an operator, three businesses with catalogs, offers and owner voice examples, then (optionally) PLAYS
real customer conversations through the real system — signed webhooks, queue, agent, pricing engine, pacing —
so every conversation, handoff, deal and metric on the dashboard was produced by the product itself. Nothing is
inserted by hand except configuration; the only simulated thing is the WhatsApp network.
--spread-days shifts finished demo conversations back in time (cosmetic: gives the charts some history)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from salesai.config import get_settings
from salesai.db import jsonb
from salesai.modules.catalog import OfferIn, PolicyIn, ProductIn, VariantIn, repo
from salesai.modules.tenants import add_simulated_number, create_business, defaults
from salesai.obs import setup_logging
from salesai.phone import wa_id
from salesai.runtime import Runtime

log = logging.getLogger("salesai.seed")
OPERATOR_PHONE = "+919999900000"

FAST_CONV = {**defaults.CONVERSATION_SETTINGS, "first_check_ms": 20, "min_quiet_complete_ms": 80, "min_quiet_incomplete_ms": 300, "default_gap_ms": 150, "max_wait_ms": 2500}
FAST_TIMING = {**defaults.TIMING_PARAMS, "read_delay": {"mu": 3.0, "sigma": 0.2, "min_ms": 10, "max_ms": 40},
               "typing_ms_per_char": {"mu": 0.5, "sigma": 0.1, "min_ms": 0, "max_ms": 2}, "part_gap": {"mu": 3.0, "sigma": 0.2, "min_ms": 10, "max_ms": 40},
               "max_total_delay_ms": 800}


def pol(disclosure: str = "fixed", price: str | None = None, floor: str | None = None, steps: int = 3, rng: tuple[str, str] | None = None,
        requires: list | None = None, round_to: str = "50", negotiable: bool = True) -> PolicyIn:
    return PolicyIn(disclosure=disclosure, list_price=D(price) if price else None, range_min=D(rng[0]) if rng else None, range_max=D(rng[1]) if rng else None,
                    negotiable=bool(floor) and negotiable, ai_may_negotiate=bool(floor) and negotiable, concession_steps=steps if floor else 0,
                    floor_price=D(floor) if floor else None, concession_requires=requires or [], round_to=D(round_to))


SAREE_SHOP = {
    "name": "Sharma Sarees & Fabrics", "owner": ("Rakesh Sharma", "+919999900001"), "number": "+919999900002",
    "profile": {"address": "Shop 14, Laxmi Road, Pune 411002 (opposite Tulsi Baug)", "hours": {"mon": "10:30-20:30", "tue": "10:30-20:30", "wed": "10:30-20:30", "thu": "10:30-20:30", "fri": "10:30-20:30", "sat": "10:30-21:00", "sun": "closed"},
                "delivery": "Free home delivery in Pune within 2 days. Courier across India in 4-6 days (charges extra).", "delivery_areas": ["Pune", "Pimpri-Chinchwad", "Mumbai"],
                "payment_modes": ["UPI", "Cash", "Debit/Credit card"], "returns": "Exchange within 7 days with the bill. No returns on stitched blouses.",
                "phone": "+91 99999 00002", "facts": ["We do free blouse stitching on silk sarees above ₹5,000.", "All our Banarasi sarees are handwoven and come with a silk-mark certificate.", "Fall and pico is free on every saree."]},
    "style": [("greeting", "Namaste ji! Sharma Sarees mein aapka swagat hai 🙏 Bataiye, kaisi saree dekhni hai?"), ("thanks", "Dhanyavaad ji! Phir padhariyega 🙏"),
              ("goodbye", "Shukriya ji, shubh din! 🙏")],
    "products": [
        ("Banarasi Silk Saree", "Handwoven pure silk with zari border. Comes with silk-mark certificate.", "sarees", ["sari", "banaras"],
         [("Red", "limited", 3, pol("fixed", "8500", "7200", 3)), ("Maroon", "in_stock", 8, pol("fixed", "8500", "7200", 3)), ("Bottle Green", "in_stock", 5, pol("fixed", "8900", "7600", 3))]),
        ("Kanjivaram Silk Saree", "Temple-border Kanjivaram, pure mulberry silk, 6.2 metres with blouse piece.", "sarees", ["kanchipuram", "kanjeevaram"],
         [("Royal Blue", "in_stock", 4, pol("after_qualifying", "14500", "12500", 2, requires=[{"type": "advance_payment"}]))]),
        ("Cotton Kurta Set", "Breathable cotton kurta with palazzo. Sizes S to XXL.", "kurtas", ["kurti", "kurta"],
         [("Standard", "in_stock", 20, pol("range", None, rng=("1200", "2400"), negotiable=False))]),
        ("Chanderi Dupatta", "Light chanderi silk dupatta with gota patti.", "dupattas", ["dupatta", "stole"], [("Assorted", "in_stock", 30, pol("starts_from", "650", negotiable=False))]),
        ("Bridal Lehenga", "Custom-made bridal lehenga with zardozi work. Made to measure.", "lehengas", ["lehnga", "bridal"], [("Custom", "made_to_order", None, pol("on_request", negotiable=False))]),
        ("Cotton Saree", "Soft handloom cotton saree for daily wear.", "sarees", ["sari"], [("Assorted", "in_stock", 40, pol("fixed", "1450", negotiable=False))]),
        ("Blouse Stitching", "Custom blouse stitching, ready in 4 days.", "services", ["blouse", "stitching"], [("Standard", "in_stock", None, pol("fixed", "350", negotiable=False))]),
    ],
    "offers": [OfferIn(name="Festive 10% on 2+ sarees", kind="percent", value=D("10"), conditions={"min_qty": 2}, ends_at=datetime.now(UTC) + timedelta(days=21)),
               OfferIn(name="Free fall & pico", kind="free_item", free_item="fall & pico", ends_at=datetime.now(UTC) + timedelta(days=60))],
}
MOBILE_SHOP = {
    "name": "Gupta Mobile Point", "owner": ("Anil Gupta", "+919999900011"), "number": "+919999900012",
    "profile": {"address": "Gala 6, Station Road, Nagpur 440001", "hours": {k: "10:00-21:00" for k in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}, "payment_modes": ["UPI", "Cash", "EMI on cards"],
                "returns": "7-day replacement for manufacturing defects.", "facts": ["All phones come with brand warranty and GST bill."]},
    "style": [], "products": [
        ("Redmi 13C 5G (128GB)", "6.74 inch display, 5000 mAh battery, brand warranty.", "phones", ["redmi", "mobile", "phone"], [("Starlight Black", "in_stock", 6, pol("fixed", "10499", "9700", 2, round_to="10"))]),
        ("Fast Charger 33W", "Original 33W fast charger with cable.", "accessories", ["charger"], [("Standard", "in_stock", 50, pol("fixed", "799", negotiable=False))]),
        ("Tempered Glass", "9H tempered glass with fitting.", "accessories", ["screen guard", "glass"], [("Standard", "in_stock", 100, pol("starts_from", "99", negotiable=False))])],
    "offers": []}
KIRANA = {
    "name": "Fresh Basket Kirana", "owner": ("Meena Patil", "+919999900021"), "number": "+919999900022",
    "profile": {"address": "Plot 3, Gandhi Nagar, Nashik", "hours": {k: "07:00-22:00" for k in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}, "delivery": "Free delivery above ₹300 within 3 km.", "payment_modes": ["UPI", "Cash"]},
    "style": [], "products": [("Basmati Rice 5kg", "Aged basmati rice, 5 kg bag.", "grocery", ["rice", "chawal"], [("5 kg", "in_stock", 40, pol("fixed", "620", negotiable=False))])], "offers": []}

# (phone, name, messages) — real conversations played through the real system
CONVERSATIONS = [
    ("+919888800001", "Priya Kulkarni", ["Hi", "Do you have Banarasi silk sarees?", "What's the price of the red one?", "That's a bit high, can you do 7000?", "ok what's the best you can do?", "I'll take it. Delivery to Kothrud, Pune?",
                                          "Flat 4, Rose Apartments, Kothrud, Pune 411038", "yes confirm"]),
    ("+919888800002", "Anjali Deshmukh", ["bhaiya banarasi saree ka rate kitna hai", "thoda kam karo na", "7500 mein de do"]),
    ("+919888800003", "सुनीता जोशी", ["नमस्ते", "आपकी दुकान कब तक खुली रहती है?", "चंदेरी दुपट्टे का रेट बताइए"]),
    ("+919888800004", "Mohan Rao", ["bridal lehenga price kya hai", "wedding is in December"]),
    ("+919888800005", "Rohit Patil", ["the saree I received last week has a tear near the border, I want a refund"]),
    ("+919888800006", "Kavita Nair", ["are you a real person or a bot?", "ok. what's the weather like in Pune today?", "do you stitch blouses?"]),
    ("+919888800007", "Deepak Wagh", ["hello", "I want", "~2 cotton kurta sets", "~for my wife's birthday"]),
    ("+919888800008", "Neha Kapoor", ["do you offer gift wrapping?", "and do you ship to Bangalore?"]),
    ("+919888800009", "Sanjay More", ["can I come to the shop tomorrow at 5pm to see kanjivaram sarees?"]),
    ("+919888800010", "Ritu Singh", ["Kanjivaram saree price?", "if I pay advance, can you reduce?", "ok send me details", "not interested right now, thanks"]),
]


async def pump(rt: Runtime, seconds: float) -> None:
    """Run relay + workers until the system is quiet (nothing pending, including delayed sends) or the time is up."""
    from salesai.config import ALL_QUEUES
    runner = rt.worker(tenant_cap=None)
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    calm = 0
    while loop.time() < end:
        n = await rt.relay.publish_batch() + await runner.run_once()
        pending = sum(s.pending + s.running for s in await rt.queue.stats(list(ALL_QUEUES)))
        calm = calm + 1 if (n == 0 and pending == 0) else 0
        if calm >= 6:
            return
        await asyncio.sleep(0.05)


async def set_pacing(rt: Runtime, business_id, fast: bool) -> None:  # noqa: ANN001
    conv, timing = (FAST_CONV, FAST_TIMING) if fast else (defaults.CONVERSATION_SETTINGS, defaults.TIMING_PARAMS)
    async with rt.db.tenant(business_id) as c:
        await c.execute("UPDATE businesses SET conversation_settings=%s, timing_params=%s WHERE id=%s", (jsonb(conv), jsonb(timing), business_id))


async def create_shop(rt: Runtime, spec: dict) -> tuple:  # noqa: ANN001
    s = rt.settings
    cb = await create_business(rt.db, s.master_key_bytes, name=spec["name"], owner_phone=spec["owner"][1], owner_name=spec["owner"][0], profile=spec["profile"], ai_enabled=True)
    nid = await add_simulated_number(rt.db, cb.business_id, spec["number"], verified_name=spec["name"])
    async with rt.db.tenant(cb.business_id) as c:
        await c.execute("UPDATE businesses SET status='active' WHERE id=%s", (cb.business_id,))
        for name, desc, cat, aliases, variants in spec["products"]:
            await repo.create_product(c, cb.business_id, ProductIn(name=name, description=desc, category=cat, attributes={"aliases": aliases},
                                                                    variants=[VariantIn(name=v[0], availability=v[1], stock_qty=v[2], policy=v[3]) for v in variants]))
        for o in spec["offers"]:
            await repo.create_offer(c, cb.business_id, o)
        for sit, reply in spec["style"]:
            await c.execute("INSERT INTO style_examples (business_id, situation, owner_reply) VALUES (%s,%s,%s)", (cb.business_id, sit, reply))
    return cb, nid


async def seed(rt: Runtime, conversations: bool, spread_days: int) -> None:
    async with rt.db.system_tx() as c:
        await c.execute("INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Platform Operator','operator') ON CONFLICT (phone) DO UPDATE SET platform_role='operator'", (OPERATOR_PHONE,))
        existing = await (await c.execute("SELECT 1 FROM businesses WHERE name=%s", (SAREE_SHOP["name"],))).fetchone()
    if existing:
        log.info("demo data already present; nothing to do")
        return
    shops = {}
    for spec in (SAREE_SHOP, MOBILE_SHOP, KIRANA):
        cb, nid = await create_shop(rt, spec)
        shops[spec["name"]] = (cb, spec)
        log.info("created %s", spec["name"])
    if conversations:
        cb, spec = shops[SAREE_SHOP["name"]]
        for b, _ in shops.values():
            await set_pacing(rt, b.business_id, True)          # play fast now, restore human pacing afterwards
        for i, (phone, name, msgs) in enumerate(CONVERSATIONS):
            for m in msgs:
                burst = m.startswith("~")                       # "~text": sent right behind the previous message (a customer typing in fragments)
                await rt.sim.user_sends(spec["number"], phone, m.lstrip("~"), name=name)
                if not burst:
                    await pump(rt, 20)
            await pump(rt, 20)
            log.info("played conversation %d/%d (%s)", i + 1, len(CONVERSATIONS), name)
        # a few conversations on the other shops so the operator console has varied tenants
        for key, phone, name, text in (("Gupta Mobile Point", "+919777700001", "Vikas Jain", "Redmi 13C price?"), ("Fresh Basket Kirana", "+919777700002", "Pooja Shinde", "basmati rice 5kg ka rate?")):
            b, sp = shops[key]
            await rt.sim.user_sends(sp["number"], phone, text, name=name)
            await pump(rt, 20)
        for b, _ in shops.values():
            await set_pacing(rt, b.business_id, False)
        if spread_days:
            async with rt.db.tenant(cb.business_id) as c:
                await _spread(c, spread_days)
    log.info("seed complete. Operator sign-in: %s   Owner sign-in: %s", OPERATOR_PHONE, SAREE_SHOP["owner"][1])


async def _spread(c, days: int) -> None:  # noqa: ANN001
    """Cosmetic: move each finished conversation back by a whole number of days (keeps its internal ordering)."""
    rng = random.Random(7)  # noqa: S311
    convs = await (await c.execute("SELECT id FROM conversations ORDER BY created_at")).fetchall()
    for i, cv in enumerate(convs):
        shift = timedelta(days=rng.randint(0, days), hours=rng.randint(0, 6)) if i % 3 else timedelta(0)
        if not shift:
            continue
        await c.execute("UPDATE messages SET created_at=created_at-%s, wa_timestamp=wa_timestamp-%s, sent_at=sent_at-%s, delivered_at=delivered_at-%s, read_at=read_at-%s WHERE conversation_id=%s", (shift, shift, shift, shift, shift, cv["id"]))
        await c.execute("UPDATE turns SET created_at=created_at-%s, completed_at=completed_at-%s WHERE conversation_id=%s", (shift, shift, cv["id"]))
        await c.execute("UPDATE deals SET created_at=created_at-%s WHERE conversation_id=%s", (shift, cv["id"]))
        await c.execute("UPDATE handoffs SET created_at=created_at-%s WHERE conversation_id=%s", (shift, cv["id"]))
        await c.execute("UPDATE conversations SET last_inbound_at=last_inbound_at-%s, last_outbound_at=last_outbound_at-%s, created_at=created_at-%s, updated_at=updated_at-%s WHERE id=%s", (shift, shift, shift, shift, cv["id"]))


async def amain(a: argparse.Namespace) -> None:
    setup_logging("INFO")
    rt = await Runtime.create(get_settings())
    try:
        await seed(rt, a.conversations, a.spread_days)
    finally:
        await rt.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--conversations", action="store_true", help="play real demo conversations through the system")
    ap.add_argument("--spread-days", type=int, default=0)
    asyncio.run(amain(ap.parse_args()))
