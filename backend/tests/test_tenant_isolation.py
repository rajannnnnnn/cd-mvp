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
    "config_proposals", "audit_log", "learning_observations",
    "support_tickets", "support_messages", "support_attachments",          # shops read and write their own tickets
    "subscriptions", "invoices", "payments",                               # written by us, readable by the shop
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
        # one row in EVERY shop table, so a cross-tenant read that returns nothing proves something (INV-5)
        await c.execute("INSERT INTO style_examples (business_id, situation, owner_reply) VALUES (%s,'greeting','Namaste!')", (bid,))
        await c.execute("INSERT INTO offers (business_id, name, kind, value) VALUES (%s,'Diwali','percent',10)", (bid,))
        await c.execute("INSERT INTO turns (business_id, conversation_id, conversation_version) VALUES (%s,%s,1)", (bid, conv["id"]))
        await c.execute("INSERT INTO negotiations (business_id, conversation_id, variant_id) VALUES (%s,%s,%s)", (bid, conv["id"], var["id"]))
        await c.execute("INSERT INTO deals (business_id, conversation_id, kind) VALUES (%s,%s,'order')", (bid, conv["id"]))
        h = await (await c.execute(
            "INSERT INTO handoffs (business_id, conversation_id, reason) VALUES (%s,%s,'unknown_answer') RETURNING id", (bid, conv["id"]))).fetchone()
        await c.execute("INSERT INTO knowledge_gaps (business_id, question) VALUES (%s,'Do you deliver?')", (bid,))
        await c.execute("INSERT INTO owner_messages (business_id, business_user_id, direction, body) VALUES (%s,%s,'out','alert')", (bid, bu["id"]))
        await c.execute("INSERT INTO config_proposals (business_id, business_user_id, summary, changes) VALUES (%s,%s,'hours','{}')", (bid, bu["id"]))
        await c.execute("INSERT INTO audit_log (business_id, actor_label, action, entity) VALUES (%s,'owner','test','business')", (bid,))
        await c.execute("INSERT INTO learning_observations (business_id, conversation_id, customer_id, handoff_id, customer_text, owner_text, source)"
                        " VALUES (%s,%s,%s,%s,'refund?','no','dashboard')", (bid, conv["id"], cust["id"], h["id"]))
        tk = await (await c.execute(
            "INSERT INTO support_tickets (business_id, subject, category) VALUES (%s,'Help','question') RETURNING id", (bid,))).fetchone()
        sm = await (await c.execute(
            "INSERT INTO support_messages (business_id, ticket_id, author, body) VALUES (%s,%s,'shop','hi') RETURNING id", (bid, tk["id"]))).fetchone()
        await c.execute("INSERT INTO support_attachments (business_id, ticket_id, message_id, content_type, size_bytes, data)"
                        " VALUES (%s,%s,%s,'image/png',1,'\\x00'::bytea)", (bid, tk["id"], sm["id"]))
        ids.update(user=bu["id"], num=num["id"], conv=conv["id"], variant=var["id"], product=prod["id"])
    async with db.system_tx() as s:                     # billing rows: written by the platform once the shop exists (committed), read by the shop
        await s.execute("INSERT INTO subscriptions (business_id, plan, status) VALUES (%s,'pilot','pilot')", (bid,))
        inv = await (await s.execute(
            """INSERT INTO invoices (business_id, number, plan, period_start, period_end, lines, subtotal_paise, gst_paise, total_paise, due_at)
               VALUES (%s,%s,'growth',now(),now()+interval '1 month','[]',100,18,118,now()) RETURNING id""",
            (bid, f"INV-{uuid.uuid4().hex[:10]}"))).fetchone()
        await s.execute("INSERT INTO payments (business_id, invoice_id, provider, provider_ref, amount_paise, status)"
                        " VALUES (%s,%s,'test',%s,118,'succeeded')", (bid, inv["id"], f"ref-{uuid.uuid4().hex[:10]}"))
    return ids


@pytest.fixture
async def two_tenants(db):
    return await make_tenant(db, "Alpha"), await make_tenant(db, "Beta")


@pytest.mark.parametrize("table", [t for t in TENANT_TABLES if t != "price_floors"])
async def test_cannot_read_other_tenants_rows(db, two_tenants, table):
    a, b = two_tenants
    async with db.tenant(a["bid"]) as c:
        rows = await (await c.execute(f"SELECT business_id FROM {table}")).fetchall()  # noqa: S608
    async with db.tenant(b["bid"]) as c:
        other = await (await c.execute(f"SELECT business_id FROM {table}")).fetchall()  # noqa: S608
    # both shops must HAVE rows here, or "I cannot see the other shop's rows" proves nothing
    assert rows and other, f"{table}: a shop has no rows, so this check would pass without proving isolation"
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
        for t in ["jobs", "webhook_events", "auth_sessions", "otp_challenges", "business_keys", "sim_messages", "operator_alerts", "platform_audit_log"]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await c.execute(f"SELECT 1 FROM {t}")  # noqa: S608
            # a failed statement aborts the txn; open a fresh one for the next table
            break
    for t in ["jobs", "webhook_events", "auth_sessions", "otp_challenges", "business_keys", "sim_messages", "operator_alerts", "platform_audit_log"]:
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



# Shop tables proven isolated elsewhere, each with the reason. Anything else that carries a business_id must be in
# TENANT_TABLES (and get a row in make_tenant) or be platform-only - never silently unchecked (INV-5).
SEPARATELY_COVERED: dict[str, str] = {
    "price_floors": "never readable by the shop role; written and probed only through SECURITY DEFINER functions (tests below)",
    "outbox": "the shop role may only INSERT, never read (test_outbox_is_write_only_for_tenant_role)",
}
PLATFORM_ONLY: dict[str, str] = {   # no grant at all to the shop role (test_platform_tables_unreachable_by_tenant_role)
    "auth_sessions": "sign-in sessions, managed by the auth service on the system role",
    "business_keys": "per-shop encryption keys; the shop role must never read its own key material",
    "jobs": "the queue; work is scheduled through the outbox, never by the shop role",
    "operator_alerts": "platform alerts about a shop, for the platform team only",
    "platform_audit_log": "the platform's own trail of actions on a shop; outlives the shop (ADR 0030)",
}


async def test_every_shop_table_is_proven_isolated_or_explicitly_accounted_for(db):
    async with db.system_tx() as c:
        rows = await (await c.execute(
            """SELECT cl.relname AS t, cl.relrowsecurity AS rls, cl.relforcerowsecurity AS forced
                 FROM pg_class cl JOIN pg_namespace ns ON ns.oid = cl.relnamespace
                WHERE ns.nspname = 'public' AND cl.relkind IN ('r', 'p')
                  AND EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public' AND table_name = cl.relname AND column_name = 'business_id')""")).fetchall()
    secured = {r["t"] for r in rows if r["rls"]}
    unsecured = {r["t"] for r in rows if not r["rls"]}
    unaccounted = secured - set(TENANT_TABLES) - set(SEPARATELY_COVERED)
    assert not unaccounted, f"row-secured shop tables with no isolation proof: {sorted(unaccounted)}"
    assert not (unsecured - set(PLATFORM_ONLY)), f"tables with business_id but NO row-level security: {sorted(unsecured - set(PLATFORM_ONLY))}"
    assert all(r["forced"] for r in rows if r["rls"] and r["t"] in TENANT_TABLES), "a tenant table's RLS is not FORCEd"
