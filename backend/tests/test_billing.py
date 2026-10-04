"""Billing: trial, plans, GST invoices, payment, proration, renewal with overage, lapse and recovery, limits, tenant isolation."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest

from salesai.modules.billing import PLANS, ManualProvider, catalogue
from tests.test_api import PRODUCT, V1, hdr, login, operator_token
from tests.test_onboarding import signup


PLANS_JSON = Path(__file__).resolve().parents[2] / "frontend" / "src" / "site" / "plans.json"


async def new_shop(world, client, name="Billing Shop"):
    _, tok = await signup(world, client)
    owner = (await client.post(f"{V1}/onboarding/business", headers=hdr(tok), json={"name": name, "owner_name": "Owner"})).json()
    return owner


def clock(world, when):
    world.rt.billing.now = lambda: when


@pytest.fixture(autouse=True)
def _real_clock(world):
    yield
    world.rt.billing.now = lambda: datetime.now(UTC)


async def test_plan_catalogue_is_public_and_the_frontend_copy_is_current(client):
    r = await client.get(f"{V1}/plans")
    assert r.status_code == 200
    body = r.json()
    assert [p["code"] for p in body["plans"]] == ["starter", "growth", "scale"] and body["gst_percent"] == 18 and body["trial_days"] == 14
    assert all(p["price_year_paise"] < 12 * p["price_month_paise"] for p in body["plans"])           # yearly is a real discount
    committed = json.loads(await anyio.Path(PLANS_JSON).read_text())
    assert committed == catalogue(), "run: python scripts/export_plans.py"


async def test_a_new_shop_starts_a_free_trial_with_growth_features(world, client):
    owner = await new_shop(world, client)
    b = (await client.get(f"{V1}/billing", headers=hdr(owner))).json()
    assert b["status"] == "trialing" and b["plan"] == "growth" and b["trial_days_left"] in (13, 14) and b["open_invoice"] is None
    assert b["limits"]["numbers"] == 2 and b["usage"]["included_conversations"] == 2000


async def test_subscribe_creates_a_gst_invoice_and_paying_activates_the_plan(world, client):
    owner = await new_shop(world, client)
    r = await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "growth", "interval": "month"})
    assert r.status_code == 200, r.text
    inv = r.json()["invoice"]
    assert inv["subtotal_paise"] == 249_900 and inv["gst_paise"] == 44_982 and inv["total_paise"] == 294_882 and inv["status"] == "open"
    again = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "growth", "interval": "month"})).json()["invoice"]
    assert again["id"] == inv["id"]                                                         # no duplicate invoices for the same choice
    paid = await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))
    assert paid.status_code == 200 and paid.json()["status"] == "paid" and paid.json()["payment"] == {"status": "succeeded", "mode": "test", "instructions": None}
    b = (await client.get(f"{V1}/billing", headers=hdr(owner))).json()
    assert b["status"] == "active" and b["plan"] == "growth" and b["trial_ends_at"] is None and b["open_invoice"] is None
    assert (await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))).json()["status"] == "paid"     # idempotent
    assert [i["status"] for i in (await client.get(f"{V1}/billing/invoices", headers=hdr(owner))).json()] == ["paid"]
    doc = await client.get(f"{V1}/billing/invoices/{inv['id']}/document", headers=hdr(owner))
    assert doc.status_code == 200 and inv["number"] in doc.text and "GST (18%)" in doc.text and "₹2,949" not in doc.text and "₹2,948.82" in doc.text
    assert (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "growth", "interval": "month"})).status_code == 409   # already on it
    assert (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "pilot", "interval": "month"})).status_code == 404


async def test_upgrade_credits_unused_time_and_downgrade_waits_for_the_period_end(world, client):
    owner = await new_shop(world, client)
    start = datetime.now(UTC)
    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "starter", "interval": "month"})).json()["invoice"]
    await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))
    clock(world, start + timedelta(days=15))                                                  # half way through the period
    up = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "scale", "interval": "month"})).json()["invoice"]
    credit = [x for x in up["lines"] if x["amount_paise"] < 0]
    assert credit and 40_000 < -credit[0]["amount_paise"] < 60_000                              # about half of Starter's 999
    assert up["subtotal_paise"] == 599_900 + credit[0]["amount_paise"]
    await client.post(f"{V1}/billing/invoices/{up['id']}/pay", headers=hdr(owner))
    down = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "starter", "interval": "month"})).json()
    assert down["scheduled"] is True and down["invoice"] is None
    b = (await client.get(f"{V1}/billing", headers=hdr(owner))).json()
    assert b["plan"] == "scale" and b["pending_plan"] == "starter"


async def test_renewal_bills_the_next_period_plus_overage_and_applies_a_scheduled_downgrade(world, client):
    owner = await new_shop(world, client)
    bid = uuid.UUID(owner["business_id"])
    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "starter", "interval": "month"})).json()["invoice"]
    await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))
    async with world.rt.db.tenant(bid) as c:      # 520 conversations handled, 20 beyond Starter's 500
        num = (await (await c.execute("INSERT INTO whatsapp_numbers (business_id, channel, phone_number_id, waba_id, display_phone, status) VALUES (%s,'simulator','sim-x','w','+919800000002','connected') RETURNING id", (bid,))).fetchone())["id"]
        for i in range(520):
            cust = (await (await c.execute("INSERT INTO customers (business_id, wa_id) VALUES (%s,%s) RETURNING id", (bid, f"9198100{i:05d}"))).fetchone())["id"]
            cv = (await (await c.execute("INSERT INTO conversations (business_id, customer_id, whatsapp_number_id) VALUES (%s,%s,%s) RETURNING id", (bid, cust, num))).fetchone())["id"]
            await c.execute("INSERT INTO turns (business_id, conversation_id, conversation_version, status) VALUES (%s,%s,1,'sent')", (bid, cv))
    b = (await client.get(f"{V1}/billing", headers=hdr(owner))).json()
    assert b["usage"]["conversations"] == 520 and b["usage"]["extra_conversations"] == 20 and b["usage"]["overage_estimate_paise"] == 20 * 300
    await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "starter", "interval": "year"})      # same plan, other interval: scheduled
    end = datetime.fromisoformat(b["current_period_end"])
    clock(world, end + timedelta(hours=1))
    done = await world.rt.billing.run_cycle()
    assert done["renewed"] >= 1                                                               # (the database is shared with other tests' shops)
    invs = (await client.get(f"{V1}/billing/invoices", headers=hdr(owner))).json()
    renewal = invs[0]
    assert renewal["status"] == "open" and renewal["billing_interval"] == "year"
    descs = [x["description"] for x in renewal["lines"]]
    assert any("Starter plan, yearly" in d for d in descs) and any("20 extra AI conversations" in d for d in descs)
    assert renewal["subtotal_paise"] == 999_000 + 20 * 300
    await world.rt.billing.run_cycle()
    assert len((await client.get(f"{V1}/billing/invoices", headers=hdr(owner))).json()) == len(invs)                      # running again issues nothing new


async def test_trial_end_pauses_the_assistant_and_paying_brings_it_back(world, client):
    owner = await new_shop(world, client)
    assert (await client.post(f"{V1}/products", headers=hdr(owner), json=PRODUCT)).status_code == 201
    assert (await client.post(f"{V1}/numbers/test", headers=hdr(owner))).status_code == 201
    assert (await client.post(f"{V1}/onboarding/go-live", headers=hdr(owner))).status_code == 200
    assert (await client.get(f"{V1}/business", headers=hdr(owner))).json()["ai_enabled"] is True

    clock(world, datetime.now(UTC) + timedelta(days=15))
    assert (await world.rt.billing.run_cycle())["trial_ended"] >= 1
    assert (await client.get(f"{V1}/business", headers=hdr(owner))).json()["ai_enabled"] is False
    b = (await client.get(f"{V1}/billing", headers=hdr(owner))).json()
    assert b["state"] == "trial_ended" and b["assistant_paused_by_billing"] is True
    r = await client.post(f"{V1}/business/ai", headers=hdr(owner), json={"enabled": True})
    assert r.status_code == 402 and r.json()["error"]["code"] == "subscription_inactive"

    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "growth", "interval": "month"})).json()["invoice"]
    await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))
    assert (await client.get(f"{V1}/business", headers=hdr(owner))).json()["ai_enabled"] is True        # back on, data untouched


async def test_unpaid_renewal_goes_past_due_then_lapses_after_the_grace_period(world, client):
    owner = await new_shop(world, client)
    assert (await client.post(f"{V1}/products", headers=hdr(owner), json=PRODUCT)).status_code == 201
    assert (await client.post(f"{V1}/numbers/test", headers=hdr(owner))).status_code == 201
    await client.post(f"{V1}/onboarding/go-live", headers=hdr(owner))
    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "starter", "interval": "month"})).json()["invoice"]
    await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))
    end = datetime.fromisoformat((await client.get(f"{V1}/billing", headers=hdr(owner))).json()["current_period_end"])
    clock(world, end + timedelta(minutes=5))
    await world.rt.billing.run_cycle()                                                        # renewal invoice issued, not yet due
    clock(world, end + timedelta(days=4))
    assert (await world.rt.billing.run_cycle())["past_due"] >= 1
    assert (await client.get(f"{V1}/billing", headers=hdr(owner))).json()["status"] == "past_due"
    assert (await client.get(f"{V1}/business", headers=hdr(owner))).json()["ai_enabled"] is True      # grace period: still running
    clock(world, end + timedelta(days=11))
    assert (await world.rt.billing.run_cycle())["lapsed"] >= 1
    assert (await client.get(f"{V1}/business", headers=hdr(owner))).json()["ai_enabled"] is False
    open_inv = (await client.get(f"{V1}/billing", headers=hdr(owner))).json()["open_invoice"]
    await client.post(f"{V1}/billing/invoices/{open_inv['id']}/pay", headers=hdr(owner))
    assert (await client.get(f"{V1}/business", headers=hdr(owner))).json()["ai_enabled"] is True
    assert (await client.get(f"{V1}/billing", headers=hdr(owner))).json()["status"] == "active"


async def test_cancel_runs_to_the_period_end_and_can_be_undone(world, client):
    owner = await new_shop(world, client)
    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "starter", "interval": "month"})).json()["invoice"]
    await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))
    b = (await client.post(f"{V1}/billing/cancel", headers=hdr(owner))).json()
    assert b["cancel_at_period_end"] is True and b["status"] == "active"
    assert (await client.post(f"{V1}/billing/resume", headers=hdr(owner))).json()["cancel_at_period_end"] is False
    await client.post(f"{V1}/billing/cancel", headers=hdr(owner))
    clock(world, datetime.fromisoformat(b["current_period_end"]) + timedelta(hours=1))
    assert (await world.rt.billing.run_cycle())["ended"] >= 1
    assert (await client.get(f"{V1}/billing", headers=hdr(owner))).json()["state"] == "canceled"


async def test_plan_limits_are_enforced_and_pilot_shops_have_none(world, client):
    owner = await new_shop(world, client)
    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "starter", "interval": "month"})).json()["invoice"]
    await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))
    ok = await client.post(f"{V1}/team", headers=hdr(owner), json={"phone": "+919811200001", "name": "Helper", "role": "staff"})
    assert ok.status_code == 201                                                              # owner + 1 = Starter's 2
    r = await client.post(f"{V1}/team", headers=hdr(owner), json={"phone": "+919811200002", "name": "Helper 2", "role": "staff"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "plan_limit" and "Starter" in r.json()["error"]["message"]
    assert (await client.post(f"{V1}/numbers/test", headers=hdr(owner))).status_code == 201
    assert (await client.post(f"{V1}/numbers/test", headers=hdr(owner))).status_code == 402        # Starter: one number

    shop = await world.make_shop("Pilot Shop")                                                # operator-created: a pilot, no limits
    tok = await login(client, shop.owner_phone)
    b = (await client.get(f"{V1}/billing", headers=hdr(tok))).json()
    assert b["status"] == "pilot" and b["limits"] == {"numbers": None, "products": None, "team": None}


async def test_manual_payments_stay_open_until_the_platform_team_confirms(world, client):
    world.rt.billing.provider = ManualProvider("Pay by UPI to pay@example and quote the number.")
    owner = await new_shop(world, client)
    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "growth", "interval": "year"})).json()["invoice"]
    r = (await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))).json()
    assert r["status"] == "open" and r["payment"]["status"] == "pending" and "UPI" in r["payment"]["instructions"]
    assert (await client.get(f"{V1}/billing", headers=hdr(owner))).json()["status"] == "trialing"
    op = await operator_token(world, client)
    assert inv["id"] in [i["id"] for i in (await client.get(f"{V1}/operator/invoices", headers=hdr(op))).json()]
    assert (await client.post(f"{V1}/operator/invoices/{inv['id']}/mark-paid", headers=hdr(owner))).status_code == 403
    done = await client.post(f"{V1}/operator/invoices/{inv['id']}/mark-paid", headers=hdr(op), params={"reference": "UTR123"})
    assert done.status_code == 200 and done.json()["status"] == "paid"
    assert (await client.get(f"{V1}/billing", headers=hdr(owner))).json()["status"] == "active"
    s = (await client.get(f"{V1}/operator/billing", headers=hdr(op))).json()
    assert s["subscriptions_by_status"].get("active", 0) >= 1 and s["mrr_paise"] >= PLANS["growth"].price_year_paise // 12


async def test_one_shop_cannot_see_or_pay_another_shops_invoices(world, client, db):
    a = await new_shop(world, client, "Shop A")
    b = await new_shop(world, client, "Shop B")
    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(a), json={"plan": "growth", "interval": "month"})).json()["invoice"]
    assert (await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(b))).status_code == 404
    assert (await client.get(f"{V1}/billing/invoices/{inv['id']}/document", headers=hdr(b))).status_code == 404
    assert (await client.get(f"{V1}/billing/invoices", headers=hdr(b))).json() == []
    async with db.tenant(uuid.UUID(b["business_id"])) as c:                                   # and the database agrees (RLS), even for a direct query
        assert await (await c.execute("SELECT * FROM invoices")).fetchall() == []
