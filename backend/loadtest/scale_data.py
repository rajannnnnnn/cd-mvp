"""Fill a scratch database with production-sized data so the schema and its indexes can be judged at scale (not on a dozen demo rows).

    python -m loadtest.scale_data --admin-url postgresql://postgres@127.0.0.1:5432/salesai_scale [--shops 50] [--products 400] [--customers 3000] [--messages 20]

The database must already be migrated (python -m salesai migrate). It is written as the superuser (row-level security does not apply), in bulk
with generate_series, so a few million rows load in about a minute. Every shop gets an owner who can sign in through the simulator:
phone +9170000<index, 5 digits> (for example +917000000001). Never point this at a database that holds real data."""
from __future__ import annotations

import argparse
import time

import psycopg

STEPS = [
    ("accounts", """INSERT INTO accounts (id, phone, name, status) SELECT s.owner, '+9170000' || lpad(s.i::text, 5, '0'), 'Owner ' || s.i, 'active' FROM scale_shops s"""),
    ("businesses", """INSERT INTO businesses (id, name, slug, status, plan, ai_enabled, created_at)
        SELECT s.id, 'Scale Shop ' || s.i, 'scale-shop-' || s.i, 'active', 'pilot', true, now() - interval '120 days' FROM scale_shops s"""),
    ("subscriptions", """INSERT INTO subscriptions (business_id, plan, status) SELECT s.id, 'pilot', 'pilot' FROM scale_shops s"""),
    ("business_users", """INSERT INTO business_users (business_id, account_id, name, role) SELECT s.id, s.owner, 'Owner ' || s.i, 'owner' FROM scale_shops s"""),
    ("whatsapp_numbers", """INSERT INTO whatsapp_numbers (id, business_id, channel, phone_number_id, waba_id, display_phone, verified_name, status)
        SELECT s.num, s.id, 'simulator', 'scale-pn-' || s.i, 'scale-waba-' || s.i, '+9171000' || lpad(s.i::text, 5, '0'), 'Scale Shop ' || s.i, 'connected' FROM scale_shops s"""),
    ("products", """INSERT INTO products (id, business_id, name, description, category, active, created_at)
        SELECT gen_random_uuid(), s.id, 'Product ' || g, 'A product that the assistant can talk about, number ' || g, (ARRAY['sarees','suits','dupattas','kurtis','fabric','jewellery','bags','shoes'])[1 + g %% 8],
               g %% 10 <> 0, now() - (random() * interval '100 days')
        FROM scale_shops s, generate_series(1, %(products)s) g"""),
    ("product_variants", """INSERT INTO product_variants (id, business_id, product_id, name, availability, stock_qty, is_default, active)
        SELECT gen_random_uuid(), p.business_id, p.id, 'Standard', 'in_stock', 10, true, true FROM products p"""),
    ("pricing_policies", """INSERT INTO pricing_policies (variant_id, business_id, disclosure, list_price, negotiable, ai_may_negotiate, concession_steps, round_to)
        SELECT v.id, v.business_id, 'fixed', 500 + (random() * 9000)::int, false, false, 0, 1 FROM product_variants v"""),
    ("customers", """INSERT INTO customers (id, business_id, wa_id, name, first_seen_at, last_seen_at)
        SELECT gen_random_uuid(), s.id, '91' || lpad((s.i * 100000 + g)::text, 10, '0'), 'Customer ' || g,
               now() - (random() * interval '90 days'), now() - (random() * interval '30 days')
        FROM scale_shops s, generate_series(1, %(customers)s) g"""),
    ("conversations", """INSERT INTO conversations (id, business_id, customer_id, whatsapp_number_id, state, version, lead_stage, last_inbound_at, last_outbound_at, created_at, updated_at)
        SELECT gen_random_uuid(), c.business_id, c.id, s.num, 'idle', 1,
               (ARRAY['new','exploring','interested','negotiating','ready_to_buy','won','lost'])[1 + (random() * 6)::int],
               c.last_seen_at, c.last_seen_at, c.first_seen_at, c.last_seen_at
        FROM customers c JOIN scale_shops s ON s.id = c.business_id"""),
    ("messages", """INSERT INTO messages (id, business_id, conversation_id, direction, sender, kind, body, status, answered, wa_timestamp, created_at)
        SELECT gen_random_uuid(), cv.business_id, cv.id, CASE WHEN g %% 2 = 1 THEN 'in' ELSE 'out' END, CASE WHEN g %% 2 = 1 THEN 'customer' ELSE 'ai' END, 'text',
               'Message ' || g || ' in a conversation about a product and its price, long enough to look like a real chat line.',
               CASE WHEN g %% 2 = 1 THEN 'received' ELSE 'read' END, true,
               cv.created_at + g * interval '3 minutes', cv.created_at + g * interval '3 minutes'
        FROM conversations cv, generate_series(1, %(messages)s) g"""),
    ("turns", """INSERT INTO turns (id, business_id, conversation_id, conversation_version, status, model, input_tokens, output_tokens, cost_micros, latency_ms, created_at, completed_at)
        SELECT gen_random_uuid(), cv.business_id, cv.id, 1, 'sent', 'local-rules', 900, 120, 0, 40, cv.created_at + g * interval '6 minutes', cv.created_at + g * interval '6 minutes'
        FROM conversations cv, generate_series(1, 3) g"""),
    ("deals", """INSERT INTO deals (id, business_id, conversation_id, kind, value, status, created_at)
        SELECT gen_random_uuid(), cv.business_id, cv.id, 'order', 500 + (random() * 5000)::int, 'pending', cv.updated_at FROM conversations cv WHERE random() < 0.2"""),
    ("handoffs", """INSERT INTO handoffs (id, business_id, conversation_id, reason, status, created_at)
        SELECT gen_random_uuid(), cv.business_id, cv.id, 'customer_asked_human', CASE WHEN random() < 0.1 THEN 'open' ELSE 'resolved' END, cv.updated_at FROM conversations cv WHERE random() < 0.1"""),
]


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--admin-url", required=True)
    a.add_argument("--shops", type=int, default=50)
    a.add_argument("--products", type=int, default=400)
    a.add_argument("--customers", type=int, default=3000)
    a.add_argument("--messages", type=int, default=20)
    a.add_argument("--from-step", default="", help="resume at this table after a failed run (the helper table scale_shops is kept until the end)")
    a = a.parse_args()
    with psycopg.connect(a.admin_url, autocommit=True) as c:
        if not a.from_step:
            if c.execute("SELECT count(*) FROM businesses WHERE slug LIKE 'scale-shop-%%'").fetchone()[0]:
                raise SystemExit("this database already holds scale data; drop and recreate it first")
            c.execute("DROP TABLE IF EXISTS scale_shops")
            c.execute("CREATE TABLE scale_shops AS SELECT g AS i, gen_random_uuid() AS id, gen_random_uuid() AS owner, gen_random_uuid() AS num FROM generate_series(1, %s) g", (a.shops,))
        params = {"products": a.products, "customers": a.customers, "messages": a.messages}
        names = [n for n, _ in STEPS]
        for name, sql in STEPS[names.index(a.from_step) if a.from_step else 0:]:
            t = time.time()
            n = c.execute(sql, params).rowcount
            print(f"{name:18} {n:>10,} rows  {time.time() - t:6.1f}s", flush=True)
        c.execute("DROP TABLE scale_shops")
        t = time.time()
        c.execute("ANALYZE")
        print(f"analyze            {time.time() - t:6.1f}s")


if __name__ == "__main__":
    main()
