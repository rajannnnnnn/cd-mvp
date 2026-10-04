"""INV-5 / INV-1: isolation enforced by the database. Every tenant table is attacked across
tenants, then the floor-price separation is proven at the privilege level."""
from __future__ import annotations

import uuid

import psycopg
import pytest

TENANT_TABLES = [
    "business_users", "whatsapp_numbers", "style_examples", "products", "product_variants",
    "pricing_policies", "price_floors", "offers", "customers", "conversations", "turns",
    "messages", "negotiations", "deals", "handoffs", "knowledge_gaps", "owner_messages",
    "config_proposals", "audit_log",
]


async def make_tenant(db, name: str) -> dict:
    """Creates a business with one row in the main tables, all through the tenant role."""
    bid = uuid.uuid4()
    ids: dict = {"bid": bid}
    async with db.tenant(bid) as c:
        await c.execute("INSERT INTO businesses (id, name) VALUES (%s, %s)", (bid, name))
        async with db.system_tx() as s:
            acc = await (await s.execute(
                "INSERT INTO accounts (phone) VALUES (%s) RETURNING id", (f"+9198{uuid.uuid4().int % 10**8:08d}",))).fetchone()
        ids["account"] = acc["id"]
        bu = await (await c.execute(
            "INSERT INTO business_users (business_id, account_id, name) VALUES (%s,%s,'Owner') RETURNING id",
            (bid, acc["id"]))).fetchone()
        num = await (await c.execute(
            "INSERT INTO whatsapp_numbers (business_id, channel, phone_number_id, waba_id, display_phone)"
            " VALUES (%s,'simulator',%s,'w','+919000000000') RETURNING id", (bid, f"pn-{bid}"))).fetchone()
        cust = await (await c.execute(
            "INSERT INTO customers (business_id, wa_id) VALUES (%s,'919111111111') RETURNING id", (bid,))).fetchone()
        conv = await (await c.execute(
            "INSERT INTO conversations (business_id, customer_id, whatsapp_number_id) VALUES (%s,%s,%s) RETURNING id",
            (bid, cust["id"], num["id"]))).fetchone()
        await c.execute("INSERT INTO messages (business_id, conversation_id, direction, sender, body)"
                        " VALUES (%s,%s,'in','customer','hi')", (bid, conv["id"]))
        prod = await (await c.execute(
            "INSERT INTO products (business_id, name) VALUES (%s,'Saree') RETURNING id", (bid,))).fetchone()
        var = await (await c.execute(
            "INSERT INTO product_variants (business_id, product_id) VALUES (%s,%s) RETURNING id", (bid, prod["id"]))).fetchone()
        await c.execute(
            "INSERT INTO pricing_policies (variant_id, business_id, disclosure, list_price, negotiable)"
            " VALUES (%s,%s,'fixed',1000,true)", (var["id"], bid))
        await c.execute("SELECT set_price_floor(%s, 800)", (var["id"],))
        ids.update(user=bu["id"], num=num["id"], conv=conv["id"], variant=var["id"], product=prod["id"])
    return ids


@pytest.fixture
async def two_tenants(db):
    return await make_tenant(db, "Alpha"), await make_tenant(db, "Beta")


@pytest.mark.parametrize("table", [t for t in TENANT_TABLES if t != "price_floors"])
async def test_cannot_read_other_tenants_rows(db, two_tenants, table):
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        rows = await (await c.execute(f"SELECT business_id FROM {table}")).fetchall()  # noqa: S608
    assert all(r["business_id"] == a["bid"] for r in rows)
    assert b["bid"] not in {r["business_id"] for r in rows}


async def test_no_tenant_context_sees_nothing(db, two_tenants):
    async with db.user.connection() as c:  # no app.business_id set
        for t in [*TENANT_TABLES[:1], "customers", "messages", "conversations"]:
            rows = await (await c.execute(f"SELECT 1 FROM {t}")).fetchall()  # noqa: S608
            assert rows == [], t
        assert await (await c.execute("SELECT 1 FROM businesses")).fetchall() == []
        assert await (await c.execute("SELECT 1 FROM accounts")).fetchall() == []


async def test_cannot_insert_for_other_tenant(db, two_tenants):
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await c.execute("INSERT INTO customers (business_id, wa_id) VALUES (%s,'x')", (b["bid"],))


async def test_cannot_update_or_delete_other_tenants_rows(db, two_tenants):
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        r = await c.execute("UPDATE products SET name='hacked' WHERE id=%s", (b["product"],))
        assert r.rowcount == 0
        r = await c.execute("DELETE FROM conversations WHERE id=%s", (b["conv"],))
        assert r.rowcount == 0
    async with db.tenant(b["bid"]) as c:
        row = await (await c.execute("SELECT name FROM products WHERE id=%s", (b["product"],))).fetchone()
        assert row["name"] == "Saree"


async def test_composite_fk_blocks_cross_tenant_reference(db, two_tenants):
    """Even a correct tenant context cannot point a row at another tenant's parent."""
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with c.transaction():
                await c.execute(
                    "INSERT INTO product_variants (business_id, product_id) VALUES (%s,%s)", (a["bid"], b["product"]))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with c.transaction():
                await c.execute(
                    "INSERT INTO messages (business_id, conversation_id, direction, sender) VALUES (%s,%s,'in','customer')",
                    (a["bid"], b["conv"]))


async def test_accounts_visible_only_for_own_members(db, two_tenants):
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        rows = await (await c.execute("SELECT id FROM accounts")).fetchall()
    assert {r["id"] for r in rows} == {a["account"]}
    assert b["account"] not in {r["id"] for r in rows}


async def test_outbox_is_write_only_for_tenant_role(db, two_tenants):
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        await c.execute("INSERT INTO outbox (business_id, event_type) VALUES (%s,'x.y')", (a["bid"],))
    async with db.tenant(a["bid"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await c.execute("SELECT * FROM outbox")
    async with db.tenant(a["bid"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await c.execute("INSERT INTO outbox (business_id, event_type) VALUES (%s,'x.y')", (b["bid"],))


async def test_platform_tables_unreachable_by_tenant_role(db):
    async with db.tenant(uuid.uuid4()) as c:
        for t in ["jobs", "webhook_events", "auth_sessions", "otp_challenges", "business_keys", "sim_messages"]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await c.execute(f"SELECT 1 FROM {t}")  # noqa: S608
            # a failed statement aborts the txn; open a fresh one for the next table
            break
    for t in ["jobs", "webhook_events", "auth_sessions", "otp_challenges", "business_keys", "sim_messages"]:
        async with db.tenant(uuid.uuid4()) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await c.execute(f"SELECT 1 FROM {t}")  # noqa: S608


# ------------------------------------------------------------------ INV-1 floors
async def test_tenant_role_cannot_read_or_write_floors_directly(db, two_tenants):
    a, _ = two_tenants
    async with db.tenant(a["bid"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await c.execute("SELECT floor_price FROM price_floors")
    async with db.tenant(a["bid"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await c.execute("UPDATE price_floors SET floor_price = 1")


async def test_floor_write_only_function_and_exists_probe(db, two_tenants):
    a, _ = two_tenants
    async with db.tenant(a["bid"]) as c:
        assert (await (await c.execute("SELECT price_floor_is_set(%s) AS s", (a["variant"],))).fetchone())["s"] is True
        await c.execute("SELECT clear_price_floor(%s)", (a["variant"],))
        assert (await (await c.execute("SELECT price_floor_is_set(%s) AS s", (a["variant"],))).fetchone())["s"] is False
        await c.execute("SELECT set_price_floor(%s, 750.50)", (a["variant"],))


async def test_public_catalog_view_has_no_floor_and_is_tenant_scoped(db, two_tenants):
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        cur = await c.execute("SELECT * FROM catalog_public")
        cols = [d.name for d in cur.description]
        rows = await cur.fetchall()
    assert not any("floor" in col for col in cols)
    assert {r["variant_id"] for r in rows} == {a["variant"]}


async def test_pricing_role_reads_only_its_tenants_floors(db, two_tenants):
    a, b = two_tenants
    async with db.pricing_tenant(a["bid"]) as c:
        rows = await (await c.execute("SELECT variant_id, floor_price FROM price_floors")).fetchall()
    assert {r["variant_id"] for r in rows} == {a["variant"]}
    async with db.pricing_tenant(a["bid"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await c.execute("SELECT 1 FROM messages")
    async with db.pricing_tenant(a["bid"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await c.execute("UPDATE price_floors SET floor_price = 1")


async def test_floor_cannot_be_set_for_other_tenants_variant(db, two_tenants):
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.InsufficientPrivilege)):
            await c.execute("SELECT set_price_floor(%s, 1)", (b["variant"],))
    async with db.pricing_tenant(b["bid"]) as c:
        row = await (await c.execute("SELECT floor_price FROM price_floors WHERE variant_id=%s", (b["variant"],))).fetchone()
    assert row["floor_price"] == 800
