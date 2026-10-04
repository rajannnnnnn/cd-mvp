"""Owner analytics and reports: figures are computed from the shop's own rows, compare with the previous period, and never expose floors."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

from tests.test_api import PRODUCT, V1, hdr
from tests.test_api import owner as make_owner


async def seed_activity(world, shop, *, days_ago: int, customers: int, deals: int, handoffs: int = 0) -> list[uuid.UUID]:
    """Customers who wrote in `days_ago` days back; the first `deals` of them ended in an order; AI replied 20 s after each first message."""
    ts = datetime.now(UTC) - timedelta(days=days_ago, hours=1)
    convs = []
    async with world.rt.db.tenant(shop.business_id) as c:
        for i in range(customers):
            cu = (await (await c.execute("INSERT INTO customers (business_id, wa_id, name, first_seen_at, last_seen_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                                         (shop.business_id, f"9198{uuid.uuid4().int % 10**8:08d}", f"=Cust {i}", ts, ts))).fetchone())["id"]
            cv = (await (await c.execute("INSERT INTO conversations (business_id, customer_id, whatsapp_number_id, lead_stage, last_inbound_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                                         (shop.business_id, cu, shop.number_id, "won" if i < deals else "exploring", ts))).fetchone())["id"]
            convs.append(cv)
            await c.execute("INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status, created_at) VALUES (%s,%s,'in','customer','text','hi','received',%s)", (shop.business_id, cv, ts))
            await c.execute("INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status, created_at, sent_at) VALUES (%s,%s,'out','ai','text','hello','sent',%s,%s)",
                            (shop.business_id, cv, ts + timedelta(seconds=20), ts + timedelta(seconds=20)))
            if i < deals:
                await c.execute("INSERT INTO deals (business_id, conversation_id, kind, items, value, status, created_at) VALUES (%s,%s,'order','[]',%s,'won',%s)", (shop.business_id, cv, 1000, ts))
            if i < handoffs:
                await c.execute("INSERT INTO handoffs (business_id, conversation_id, reason, created_at) VALUES (%s,%s,'complaint',%s)", (shop.business_id, cv, ts))
    return convs


async def test_analytics_kpis_compare_with_the_previous_period_and_never_leak_floors(world, client):
    shop, tok = await make_owner(world, client, "Analytics Shop")
    await client.post(f"{V1}/products", headers=hdr(tok), json=PRODUCT)
    await seed_activity(world, shop, days_ago=2, customers=10, deals=3, handoffs=2)          # this period
    await seed_activity(world, shop, days_ago=40, customers=5, deals=1)                      # the previous 30 days
    r = await client.get(f"{V1}/analytics", headers=hdr(tok), params={"days": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    k, pk = body["kpis"], body["previous"]
    assert k["conversations"] == 10 and k["deals"] == 3 and k["deal_value"] == 3000 and k["handoffs"] == 2
    assert k["handoff_rate"] == 0.2 and k["conversion_rate"] == 0.3 and k["ai_replies"] == 10 and k["customer_messages"] == 10
    assert 19 <= k["avg_first_reply_s"] <= 21 and k["messages_per_conversation"] == 1.0
    assert pk["conversations"] == 5 and pk["deals"] == 1
    assert len(body["series"]) == 30 and sum(x["conversations"] for x in body["series"]) == 10
    assert {x["label"]: x["n"] for x in body["funnel"]} == {"won": 3, "exploring": 7}
    assert body["handoff_reasons"] == [{"label": "complaint", "n": 2}]
    assert body["deals_by_kind"] == [{"kind": "order", "n": 3, "confirmed": 3, "value": 3000.0}] and len(body["busiest_hours"]) == 24
    assert "812.34" not in r.text and "floor" not in r.text.lower()                           # INV-1


async def test_reports_use_an_explicit_range_and_csv_exports_are_safe_for_spreadsheets(world, client):
    shop, tok = await make_owner(world, client, "Report Shop")
    await seed_activity(world, shop, days_ago=3, customers=4, deals=2)
    today = datetime.now(UTC).date()
    q = {"start": str(today - timedelta(days=7)), "end": str(today)}
    r = (await client.get(f"{V1}/reports/summary", headers=hdr(tok), params=q)).json()
    assert r["days"] == 8 and r["kpis"]["conversations"] == 4
    assert (await client.get(f"{V1}/reports/summary", headers=hdr(tok), params={"start": "2026-01-10", "end": "2026-01-01"})).status_code == 422
    assert (await client.get(f"{V1}/reports/summary", headers=hdr(tok), params={"start": "2024-01-01", "end": "2026-01-01"})).status_code == 422

    for kind, rows in (("conversations", 4), ("deals", 2), ("customers", 4)):
        c = await client.get(f"{V1}/reports/export/{kind}.csv", headers=hdr(tok), params=q)
        assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv") and "attachment" in c.headers["content-disposition"]
        parsed = list(csv.DictReader(io.StringIO(c.text)))
        assert len(parsed) == rows
    parsed = list(csv.DictReader(io.StringIO((await client.get(f"{V1}/reports/export/customers.csv", headers=hdr(tok), params=q)).text)))
    assert all(not row["name"].startswith("=") for row in parsed) and parsed[0]["name"].startswith("'=")        # formula injection neutralised
    assert (await client.get(f"{V1}/reports/export/messages.csv", headers=hdr(tok), params=q)).status_code == 422


async def test_analytics_are_per_shop(world, client):
    a, ta = await make_owner(world, client, "Shop One")
    b, tb = await make_owner(world, client, "Shop Two")
    await seed_activity(world, a, days_ago=1, customers=6, deals=1)
    assert (await client.get(f"{V1}/analytics", headers=hdr(ta))).json()["kpis"]["conversations"] == 6
    assert (await client.get(f"{V1}/analytics", headers=hdr(tb))).json()["kpis"]["conversations"] == 0
    assert (await client.get(f"{V1}/analytics")).status_code == 401
