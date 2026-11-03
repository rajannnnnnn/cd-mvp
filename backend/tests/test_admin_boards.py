"""ADR 0028: the founder's four boards. Aggregates only: no customer's words or number, no floor price, ever. The database is
shared across the suite, so each test measures what ITS data adds (before and after) rather than absolute totals."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from tests.test_analytics import seed_activity
from tests.test_api import V1, hdr, operator_token, owner

B = f"{V1}/operator/boards"


async def board(client, op, name: str, **params) -> dict:
    r = await client.get(f"{B}/{name}", headers=hdr(op), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def grew(before: dict, after: dict, *path: str) -> float:
    def dig(d):
        for p in path:
            d = d[p]
        return d
    return dig(after) - dig(before)


async def test_the_boards_need_the_admin_session(world, client):
    shop, tok = await owner(world, client, "Board Guard")
    for name in ("sales", "growth", "money", "health"):
        assert (await client.get(f"{B}/{name}")).status_code == 401
        assert (await client.get(f"{B}/{name}", headers=hdr(tok))).status_code == 401           # a shop token does not even verify


# ---------------------------------------------------------------- sales outcome

async def test_the_sales_board_measures_autonomy_commitment_and_wins(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Sales Board Shop")
    before = await board(client, op, "sales", days=30)
    await seed_activity(world, shop, days_ago=2, customers=10, deals=3, handoffs=2)            # 10 chats; 3 became orders; 2 needed the owner
    after = await board(client, op, "sales", days=30)
    assert grew(before, after, "conversations") == 10
    assert grew(before, after, "autonomous") == 8                                             # no handoff, no owner message
    assert grew(before, after, "commitments") == 3                                            # chats that ended in an order or a visit
    assert grew(before, after, "wins", "count") == 3 and grew(before, after, "wins", "value_inr") == 3000
    assert grew(before, after, "handoffs", "complaint") == 2
    assert 0 <= after["autonomous_pct"] <= 100 and 0 <= after["commitment_pct"] <= 100


async def test_chats_older_than_the_window_do_not_count(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Old Chats Shop")
    before = await board(client, op, "sales", days=7)
    await seed_activity(world, shop, days_ago=40, customers=5, deals=2)
    assert grew(before, await board(client, op, "sales", days=7), "conversations") == 0
    assert grew(before, await board(client, op, "sales", days=90), "conversations") >= 0


async def test_an_owner_who_steps_in_means_the_chat_was_not_autonomous(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Stepped In Shop")
    before = await board(client, op, "sales", days=30)
    convs = await seed_activity(world, shop, days_ago=1, customers=4, deals=0)
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status) VALUES (%s,%s,'out','owner','text','ok','sent')",
                        (shop.business_id, convs[0]))
    after = await board(client, op, "sales", days=30)
    assert grew(before, after, "conversations") == 4 and grew(before, after, "autonomous") == 3


async def test_replies_the_checks_stopped_are_counted_by_kind_and_what_is_not_measured_says_so(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Checks Shop")
    before = await board(client, op, "sales", days=30)
    convs = await seed_activity(world, shop, days_ago=1, customers=1, deals=0)
    attempts = {"checks": {"attempts": [{"ok": False, "failures": ["price: amount 799 was not issued by the pricing engine"]},
                                        {"ok": True, "failures": []}]}}
    async with world.rt.db.tenant(shop.business_id) as c:
        for i in range(3):
            await c.execute("INSERT INTO turns (business_id, conversation_id, conversation_version, status, decision) VALUES (%s,%s,%s,'sent',%s)",
                            (shop.business_id, convs[0], i, json.dumps(attempts if i < 2 else {"checks": {"attempts": [{"ok": True, "failures": []}]}})))
    after = await board(client, op, "sales", days=30)
    assert grew(before, after, "turns") == 3 and grew(before, after, "caught", "price") == 2
    assert after["caught"].keys() >= {"price", "urgency", "human_claim", "identity", "internal", "sensitive"}
    # figures the platform does not record yet are null with a reason, never a reassuring zero
    for key in ("reached_customers_unauthorized_price", "bot_suspicion_rate", "wrong_fact_reports"):
        assert after["not_measured"][key]


# ---------------------------------------------------------------- growth

async def test_the_growth_funnel_counts_shops_through_each_step(world, client):
    op = await operator_token(world, client)
    before = await board(client, op, "growth", days=30)
    a, b, c_ = await world.make_shop("Funnel A"), await world.make_shop("Funnel B"), await world.make_shop("Funnel C")
    await seed_activity(world, b, days_ago=1, customers=2, deals=1)                           # B: AI replied, and a commitment came
    await seed_activity(world, c_, days_ago=1, customers=1, deals=0)                          # C: AI replied, no commitment
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO subscriptions (business_id, plan, status) VALUES (%s,'starter','active') ON CONFLICT (business_id) DO UPDATE SET status='active'", (b.business_id,))
    after = await board(client, op, "growth", days=30)
    f = lambda k: grew(before, after, "funnel", k)  # noqa: E731
    assert (f("signed_up"), f("number_connected"), f("first_ai_reply"), f("first_commitment"), f("paid")) == (3, 3, 2, 1, 1)
    assert a.business_id != b.business_id
    assert after["funnel_pct"]["first_ai_reply"] is not None


async def test_growth_reports_trials_churn_and_support_load(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Churn Shop")
    before = await board(client, op, "growth", days=30)
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO subscriptions (business_id, plan, status, trial_ends_at) VALUES (%s,'starter','trialing', now() + interval '3 days')", (shop.business_id,))
    after = await board(client, op, "growth", days=30)
    assert grew(before, after, "subscriptions", "trialing") == 1
    assert {"churned_in_window", "tickets_opened", "tickets_open_now", "signups_by_week"} <= set(after)


# ---------------------------------------------------------------- money

async def test_the_money_board_ties_to_billing_and_works_out_margin_per_shop(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Money Shop")
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO subscriptions (business_id, plan, status) VALUES (%s,'starter','active') ON CONFLICT (business_id) DO UPDATE SET status='active', plan='starter'", (shop.business_id,))
    convs = await seed_activity(world, shop, days_ago=1, customers=1, deals=0)
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("INSERT INTO turns (business_id, conversation_id, conversation_version, status, cost_micros) VALUES (%s,%s,1,'sent',2500000)", (shop.business_id, convs[0]))
    m = await board(client, op, "money", days=30)
    summary = (await client.get(f"{V1}/operator/billing", headers=hdr(op))).json()
    assert m["mrr_inr"] == summary["mrr_paise"] / 100
    mine = next(r for r in m["by_shop"] if r["business_id"] == str(shop.business_id))
    assert mine["model_cost_inr"] == 2.5 and mine["revenue_inr"] > 0
    assert round(mine["margin_inr"], 4) == round(mine["revenue_inr"] - mine["model_cost_inr"] - mine["channel_cost_inr"], 4)
    assert m["arpu_inr"] is not None and m["paying_shops"] >= 1
    assert m["cost"]["model_inr"] >= 2.5


# ---------------------------------------------------------------- health

async def test_the_health_board_shows_queues_alerts_numbers_and_failure_rates(world, client):
    op = await operator_token(world, client)
    shop = await world.make_shop("Health Shop")
    before = await board(client, op, "health", days=1)
    from salesai.alerts import raise_alert
    async with world.rt.db.system_tx() as c:
        await raise_alert(c, "high", "quality_drop", "Quality is now yellow", business_id=shop.business_id)
        await c.execute("UPDATE whatsapp_numbers SET quality_rating='yellow' WHERE id=%s", (shop.number_id,))
    convs = await seed_activity(world, shop, days_ago=0, customers=1, deals=0)
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("INSERT INTO turns (business_id, conversation_id, conversation_version, status, decision) VALUES (%s,%s,1,'sent',%s)",
                        (shop.business_id, convs[0], json.dumps({"llm": {"calls": 4, "failed": 1}})))
        await c.execute("INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status) VALUES (%s,%s,'out','ai','text','x','failed')",
                        (shop.business_id, convs[0]))
    h = await board(client, op, "health", days=1)
    assert {q["queue"] for q in h["queues"]} >= {"inbound.events", "conversation.turns", "outbound.actions"}
    assert all({"pending", "oldest_age_s", "dead"} <= set(q) for q in h["queues"])
    assert grew(before, h, "alerts", "high") == 1
    assert any(n["business_name"] == "Health Shop" and n["quality"] == "yellow" for n in h["numbers_needing_attention"])
    assert grew(before, h, "llm", "calls") == 4 and grew(before, h, "llm", "failed") == 1
    assert grew(before, h, "sends", "failed") == 1
    assert h["not_measured"]["webhook_p99_ms"] and h["not_measured"]["eval_pass_rate"]


# ---------------------------------------------------------------- privacy

async def test_no_board_ever_carries_a_customers_words_number_or_a_floor(world, client):
    op = await operator_token(world, client)
    shop, tok = await owner(world, client, "Private Board Shop")
    secret_text, secret_name = f"my-secret-words-{uuid.uuid4().hex}", f"Private Person {uuid.uuid4().hex[:8]}"
    wa = f"9198{uuid.uuid4().int % 10**8:08d}"
    ts = datetime.now(UTC) - timedelta(hours=2)
    async with world.rt.db.tenant(shop.business_id) as c:
        cu = (await (await c.execute("INSERT INTO customers (business_id, wa_id, name) VALUES (%s,%s,%s) RETURNING id", (shop.business_id, wa, secret_name))).fetchone())["id"]
        cv = (await (await c.execute("INSERT INTO conversations (business_id, customer_id, whatsapp_number_id, last_inbound_at, summary) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                                     (shop.business_id, cu, shop.number_id, ts, secret_text))).fetchone())["id"]
        await c.execute("INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status, created_at) VALUES (%s,%s,'in','customer','text',%s,'received',%s)",
                        (shop.business_id, cv, secret_text, ts))
        await c.execute("INSERT INTO deals (business_id, conversation_id, kind, items, details, value, status) VALUES (%s,%s,'order','[]',%s,500,'won')",
                        (shop.business_id, cv, json.dumps({"address": secret_text})))
    await client.post(f"{V1}/products", headers=hdr(tok), json={"name": "Floor Saree", "category": "sarees", "variants": [{"name": "Red", "availability": "limited", "stock_qty": 3,
                       "policy": {"disclosure": "fixed", "list_price": "1000", "negotiable": True, "ai_may_negotiate": True, "concession_steps": 3, "floor_price": "731.77", "round_to": "10"}}]})
    for name in ("sales", "growth", "money", "health"):
        text = json.dumps(await board(client, op, name, days=30))
        for needle in (secret_text, secret_name, wa, wa[-8:], "731.77"):
            assert needle not in text, f"{name} board leaked {needle!r}"
