"""Catalog persistence (tenant transaction in, plain data out). Floor values are written through the
SECURITY DEFINER functions and are never read back here (INV-1)."""
from __future__ import annotations

import re
import uuid
from typing import Any

from salesai.db import Conn, jsonb
from salesai.modules.catalog.models import (
    OfferIn,
    OfferOut,
    PolicyIn,
    PolicyOut,
    ProductIn,
    ProductOut,
    ProductPatch,
    VariantIn,
    VariantOut,
)

STOP = {"the", "and", "for", "you", "any", "can", "have", "has", "with", "this", "that", "what", "which", "show", "your",
        "hai", "kya", "ka", "ki", "ke", "me", "mein", "se", "ko", "aur", "yeh", "ye", "wo", "hain", "price", "cost", "rate",
        "available", "stock", "want", "need", "please", "pls", "hello", "hii", "hey"}


async def audit(c: Conn, bid: uuid.UUID, actor_account_id: uuid.UUID | None, actor_label: str, action: str,
                entity: str, entity_id: str | None, detail: dict[str, Any] | None = None) -> None:
    """Audit trail (FR: pricing and settings changes with actor and time). `detail` must never carry a floor value."""
    await c.execute(
        "INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity, entity_id, detail) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (bid, actor_account_id, actor_label, action, entity, entity_id, jsonb(detail or {})))


async def _write_policy(c: Conn, bid: uuid.UUID, variant_id: uuid.UUID, p: PolicyIn) -> None:
    await c.execute(
        """INSERT INTO pricing_policies (variant_id, business_id, disclosure, currency, list_price, range_min, range_max,
               negotiable, ai_may_negotiate, concession_steps, concession_requires, round_to)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (variant_id) DO UPDATE SET disclosure=EXCLUDED.disclosure, currency=EXCLUDED.currency,
               list_price=EXCLUDED.list_price, range_min=EXCLUDED.range_min, range_max=EXCLUDED.range_max,
               negotiable=EXCLUDED.negotiable, ai_may_negotiate=EXCLUDED.ai_may_negotiate,
               concession_steps=EXCLUDED.concession_steps, concession_requires=EXCLUDED.concession_requires,
               round_to=EXCLUDED.round_to""",
        (variant_id, bid, p.disclosure, p.currency, p.list_price, p.range_min, p.range_max, p.negotiable,
         p.ai_may_negotiate, p.concession_steps, jsonb([r.model_dump(exclude_none=True) for r in p.concession_requires]), p.round_to))
    if p.clear_floor:
        await c.execute("SELECT clear_price_floor(%s)", (variant_id,))
    elif p.floor_price is not None:
        await c.execute("SELECT set_price_floor(%s,%s)", (variant_id, p.floor_price))
    if p.ai_may_negotiate:
        has_floor = (await (await c.execute("SELECT price_floor_is_set(%s) AS s", (variant_id,))).fetchone())["s"]
        if not has_floor:
            raise ValueError("AI negotiation needs a floor price")


async def create_product(c: Conn, bid: uuid.UUID, data: ProductIn, *, actor: uuid.UUID | None = None) -> uuid.UUID:
    row = await (await c.execute(
        "INSERT INTO products (business_id, name, description, category, attributes, active) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (bid, data.name, data.description, data.category, jsonb(data.attributes), data.active))).fetchone()
    pid = row["id"]
    for v in data.variants:
        await add_variant(c, bid, pid, v)
    await audit(c, bid, actor, "owner", "product.created", "product", str(pid), {"name": data.name})
    return pid


async def add_variant(c: Conn, bid: uuid.UUID, product_id: uuid.UUID, v: VariantIn, *, actor: uuid.UUID | None = None) -> uuid.UUID:
    row = await (await c.execute(
        """INSERT INTO product_variants (business_id, product_id, name, attributes, availability, stock_qty, is_default, active)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (bid, product_id, v.name, jsonb(v.attributes), v.availability, v.stock_qty, v.is_default, v.active))).fetchone()
    await _write_policy(c, bid, row["id"], v.policy)
    await audit(c, bid, actor, "owner", "pricing.updated", "variant", str(row["id"]),
                {"disclosure": v.policy.disclosure, "floor": "changed" if (v.policy.floor_price is not None or v.policy.clear_floor) else "unchanged"})
    return row["id"]


async def set_policy(c: Conn, bid: uuid.UUID, variant_id: uuid.UUID, p: PolicyIn, *, actor: uuid.UUID | None = None) -> None:
    if (await (await c.execute("SELECT 1 FROM product_variants WHERE id=%s", (variant_id,))).fetchone()) is None:
        raise LookupError("variant not found")
    await _write_policy(c, bid, variant_id, p)
    await audit(c, bid, actor, "owner", "pricing.updated", "variant", str(variant_id),
                {"disclosure": p.disclosure, "list_price": str(p.list_price) if p.list_price is not None else None,
                 "negotiable": p.negotiable, "ai_may_negotiate": p.ai_may_negotiate,
                 "floor": "changed" if (p.floor_price is not None or p.clear_floor) else "unchanged"})


async def update_variant(c: Conn, bid: uuid.UUID, variant_id: uuid.UUID, fields: dict[str, Any], *, actor: uuid.UUID | None = None) -> None:
    allowed = {"name", "attributes", "availability", "stock_qty", "is_default", "active"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=%s")
            args.append(jsonb(v) if k == "attributes" else v)
    if sets:
        r = await c.execute(f"UPDATE product_variants SET {', '.join(sets)} WHERE id=%s", (*args, variant_id))  # noqa: S608
        if r.rowcount == 0:
            raise LookupError("variant not found")
        await audit(c, bid, actor, "owner", "variant.updated", "variant", str(variant_id), {"fields": sorted(fields)})


async def update_product(c: Conn, bid: uuid.UUID, product_id: uuid.UUID, patch: ProductPatch, *, actor: uuid.UUID | None = None) -> None:
    data = patch.model_dump(exclude_unset=True)
    if not data:
        return
    sets = ", ".join(f"{k}=%s" for k in data)
    args = [jsonb(v) if k == "attributes" else v for k, v in data.items()]
    r = await c.execute(f"UPDATE products SET {sets} WHERE id=%s", (*args, product_id))  # noqa: S608
    if r.rowcount == 0:
        raise LookupError("product not found")
    await audit(c, bid, actor, "owner", "product.updated", "product", str(product_id), {"fields": sorted(data)})


async def delete_product(c: Conn, bid: uuid.UUID, product_id: uuid.UUID, *, actor: uuid.UUID | None = None) -> None:
    r = await c.execute("DELETE FROM products WHERE id=%s", (product_id,))
    if r.rowcount == 0:
        raise LookupError("product not found")
    await audit(c, bid, actor, "owner", "product.deleted", "product", str(product_id))


def _policy_out(r: dict[str, Any], floor_set: bool) -> PolicyOut:
    return PolicyOut(disclosure=r["disclosure"], currency=r["currency"], list_price=r["list_price"], range_min=r["range_min"],
                     range_max=r["range_max"], negotiable=r["negotiable"], ai_may_negotiate=r["ai_may_negotiate"],
                     concession_steps=r["concession_steps"], concession_requires=r["concession_requires"],
                     round_to=r["round_to"], floor_set=floor_set)


async def _hydrate(c: Conn, products: list[dict[str, Any]]) -> list[ProductOut]:
    if not products:
        return []
    ids = [p["id"] for p in products]
    vrows = await (await c.execute(
        """SELECT v.*, pp.disclosure, pp.currency, pp.list_price, pp.range_min, pp.range_max, pp.negotiable, pp.ai_may_negotiate,
                  pp.concession_steps, pp.concession_requires, pp.round_to, (pp.variant_id IS NOT NULL) AS has_policy,
                  price_floor_is_set(v.id) AS floor_set
           FROM product_variants v LEFT JOIN pricing_policies pp ON pp.variant_id = v.id
           WHERE v.product_id = ANY(%s) ORDER BY v.is_default DESC, v.created_at""", (ids,))).fetchall()
    by_prod: dict[uuid.UUID, list[VariantOut]] = {}
    for r in vrows:
        by_prod.setdefault(r["product_id"], []).append(VariantOut(
            id=r["id"], name=r["name"], attributes=r["attributes"], availability=r["availability"], stock_qty=r["stock_qty"],
            is_default=r["is_default"], active=r["active"], policy=_policy_out(r, r["floor_set"]) if r["has_policy"] else None))
    return [ProductOut(id=p["id"], name=p["name"], description=p["description"], category=p["category"], attributes=p["attributes"],
                       active=p["active"], variants=by_prod.get(p["id"], []), created_at=p["created_at"]) for p in products]


async def get_product(c: Conn, product_id: uuid.UUID) -> ProductOut | None:
    rows = await (await c.execute("SELECT * FROM products WHERE id=%s", (product_id,))).fetchall()
    out = await _hydrate(c, rows)
    return out[0] if out else None


async def list_products(c: Conn, *, search: str | None = None, category: str | None = None,
                        active: bool | None = None, limit: int = 50, offset: int = 0) -> tuple[list[ProductOut], int]:
    where: list[str] = ["TRUE"]
    args: list[Any] = []
    if search:
        where.append("(name ILIKE %s OR description ILIKE %s OR category ILIKE %s)")
        args += [f"%{search}%"] * 3
    if category:
        where.append("category = %s")
        args.append(category)
    if active is not None:
        where.append("active = %s")
        args.append(active)
    cond = " AND ".join(where)
    total = (await (await c.execute(f"SELECT count(*) AS n FROM products WHERE {cond}", args or None)).fetchone())["n"]  # noqa: S608
    rows = await (await c.execute(f"SELECT * FROM products WHERE {cond} ORDER BY name LIMIT %s OFFSET %s", (*args, limit, offset))).fetchall()  # noqa: S608
    return await _hydrate(c, rows), total


# ---------------------------------------------------------------- offers
async def create_offer(c: Conn, bid: uuid.UUID, o: OfferIn, *, actor: uuid.UUID | None = None) -> uuid.UUID:
    row = await (await c.execute(
        """INSERT INTO offers (business_id, variant_id, name, kind, value, free_item, conditions, starts_at, ends_at, may_cross_floor, active)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (bid, o.variant_id, o.name, o.kind, o.value, o.free_item, jsonb(o.conditions), o.starts_at, o.ends_at, o.may_cross_floor, o.active))).fetchone()
    await audit(c, bid, actor, "owner", "offer.created", "offer", str(row["id"]), {"name": o.name, "kind": o.kind})
    return row["id"]


async def list_offers(c: Conn) -> list[OfferOut]:
    rows = await (await c.execute("SELECT * FROM offers ORDER BY created_at DESC")).fetchall()
    return [OfferOut(**{k: r[k] for k in ("id", "created_at", "name", "kind", "value", "free_item", "variant_id", "conditions",
                                          "starts_at", "ends_at", "may_cross_floor", "active")}) for r in rows]


async def update_offer(c: Conn, bid: uuid.UUID, offer_id: uuid.UUID, o: OfferIn, *, actor: uuid.UUID | None = None) -> None:
    r = await c.execute(
        """UPDATE offers SET variant_id=%s, name=%s, kind=%s, value=%s, free_item=%s, conditions=%s, starts_at=%s, ends_at=%s,
                  may_cross_floor=%s, active=%s WHERE id=%s""",
        (o.variant_id, o.name, o.kind, o.value, o.free_item, jsonb(o.conditions), o.starts_at, o.ends_at, o.may_cross_floor, o.active, offer_id))
    if r.rowcount == 0:
        raise LookupError("offer not found")
    await audit(c, bid, actor, "owner", "offer.updated", "offer", str(offer_id), {"name": o.name})


async def delete_offer(c: Conn, bid: uuid.UUID, offer_id: uuid.UUID, *, actor: uuid.UUID | None = None) -> None:
    r = await c.execute("DELETE FROM offers WHERE id=%s", (offer_id,))
    if r.rowcount == 0:
        raise LookupError("offer not found")
    await audit(c, bid, actor, "owner", "offer.deleted", "offer", str(offer_id))


# ---------------------------------------------------------------- retrieval for the agent (public view only)
def query_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9ऀ-ॿ]+", (text or "").lower())
    return [w for w in words if len(w) >= 3 and w not in STOP][:12]


async def retrieve(c: Conn, text: str, *, limit: int = 8, small_catalog: int = 30) -> list[dict[str, Any]]:
    """Relevant variants for the customer's text, from catalog_public (no floor exists there).
    Small catalogs are returned whole; large ones are ranked by trigram word similarity."""
    n = (await (await c.execute("SELECT count(*) AS n FROM catalog_public")).fetchone())["n"]
    if n <= small_catalog:
        return await (await c.execute("SELECT * FROM catalog_public ORDER BY product_name, variant_name")).fetchall()
    toks = query_tokens(text)
    if not toks:
        return await (await c.execute("SELECT * FROM catalog_public ORDER BY product_name LIMIT %s", (limit,))).fetchall()
    return await (await c.execute(
        """SELECT *, (SELECT coalesce(sum(word_similarity(t, hay)), 0) FROM unnest(%s::text[]) t) AS score
           FROM (SELECT cp.*, lower(cp.product_name || ' ' || coalesce(cp.description,'') || ' ' || coalesce(cp.category,'') || ' ' || cp.variant_name) AS hay
                 FROM catalog_public cp) s
           WHERE EXISTS (SELECT 1 FROM unnest(%s::text[]) t WHERE word_similarity(t, hay) > 0.4)
           ORDER BY score DESC LIMIT %s""", (toks, toks, limit))).fetchall()
