"""Per-tenant data export and hard delete (NFR-10, DPDP). Export never includes floor prices, tokens or secrets."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from salesai.db import Database
from salesai.modules.tenants.audit import platform_audit
from salesai.queue.base import Queue

EXPORT_TABLES = [
    ("business", "SELECT id, name, timezone, status, ai_enabled, plan, profile, sales_settings, conversation_settings, timing_params, limits, created_at FROM businesses"),
    ("team", "SELECT bu.id, bu.name, a.phone, bu.role, bu.notify FROM business_users bu JOIN accounts a ON a.id=bu.account_id"),
    ("numbers", "SELECT id, channel, display_phone, verified_name, status, quality_rating, coexistence, created_at FROM whatsapp_numbers"),
    ("products", "SELECT * FROM products"),
    ("variants", "SELECT * FROM product_variants"),
    ("pricing_policies", "SELECT variant_id, disclosure, currency, list_price, range_min, range_max, negotiable, ai_may_negotiate, concession_steps, concession_requires, round_to FROM pricing_policies"),
    ("offers", "SELECT * FROM offers"),
    ("style_examples", "SELECT * FROM style_examples"),
    ("customers", "SELECT * FROM customers"),
    ("conversations", "SELECT * FROM conversations"),
    ("messages", "SELECT id, conversation_id, direction, sender, kind, body, status, wa_timestamp, sent_at, delivered_at, read_at, created_at FROM messages ORDER BY created_at"),
    ("turns", "SELECT id, conversation_id, status, decision, signals, model, prompt_versions, input_tokens, output_tokens, cost_micros, latency_ms, created_at FROM turns"),
    ("negotiations", "SELECT * FROM negotiations"),
    ("deals", "SELECT * FROM deals"),
    ("handoffs", "SELECT * FROM handoffs"),
    ("knowledge_gaps", "SELECT * FROM knowledge_gaps"),
    ("learning_observations", "SELECT id, conversation_id, customer_id, handoff_id, reason, customer_text, owner_text, source, context, created_at FROM learning_observations"),
    ("support_tickets", "SELECT id, number, subject, category, status, created_at, updated_at FROM support_tickets"),
    ("support_messages", "SELECT id, ticket_id, author, body, created_at FROM support_messages ORDER BY id"),
    ("audit_log", "SELECT * FROM audit_log"),
]


async def export_tenant(db: Database, business_id: uuid.UUID) -> dict[str, Any]:
    out: dict[str, Any] = {"exported_at": datetime.now(UTC).isoformat(), "note": "Floor prices, access tokens and secrets are never exported."}
    async with db.tenant(business_id) as c:
        for name, sql in EXPORT_TABLES:
            out[name] = await (await c.execute(sql)).fetchall()
    return json.loads(json.dumps(out, default=str))


async def delete_tenant(db: Database, business_id: uuid.UUID, queue: Queue | None = None) -> dict[str, int]:
    """Hard delete: the tenant's rows cascade; platform-side traces (raw webhooks, simulator ledger, outbox, orphaned accounts) are purged too."""
    counts: dict[str, int] = {}
    if queue is not None:
        counts["jobs"] = await queue.purge_business(business_id)       # whatever backend holds them
    async with db.system_tx() as s:
        nums = await (await s.execute("SELECT id, phone_number_id, display_phone FROM whatsapp_numbers WHERE business_id=%s", (business_id,))).fetchall()
        members = await (await s.execute("SELECT account_id FROM business_users WHERE business_id=%s", (business_id,))).fetchall()
        for n in nums:
            r = await s.execute("DELETE FROM webhook_events WHERE payload::text LIKE %s", (f'%"phone_number_id": "{n["phone_number_id"]}"%',))
            counts["webhook_events"] = counts.get("webhook_events", 0) + r.rowcount
            r = await s.execute("DELETE FROM sim_messages WHERE business_phone=%s", (n["display_phone"],))
            counts["sim_messages"] = counts.get("sim_messages", 0) + r.rowcount
        counts["outbox"] = (await s.execute("DELETE FROM outbox WHERE business_id=%s", (business_id,))).rowcount
        if nums:
            await s.execute("DELETE FROM rate_buckets WHERE key = ANY(%s)", ([f"num:{n['id']}" for n in nums],))
        counts["businesses"] = (await s.execute("DELETE FROM businesses WHERE id=%s", (business_id,))).rowcount
        ids = [m["account_id"] for m in members]
        if ids:
            counts["accounts"] = (await s.execute(
                """DELETE FROM accounts a WHERE a.id = ANY(%s) AND a.platform_role IS NULL
                   AND NOT EXISTS (SELECT 1 FROM business_users bu WHERE bu.account_id = a.id)""", (ids,))).rowcount
    return counts


# ------------------------------------------------------------------ scheduled deletion (60-day grace)
async def request_deletion(db: Database, business_id: uuid.UUID, account_id: uuid.UUID | None, grace_days: int, now: datetime | None = None) -> datetime:
    """Record that the owner wants the shop erased `grace_days` from now, pause the assistant and stop renewals. Asking again keeps the first date."""
    now = now or datetime.now(UTC)
    async with db.system_tx() as c:
        biz = await (await c.execute("SELECT ai_enabled FROM businesses WHERE id=%s FOR UPDATE", (business_id,))).fetchone()
        if biz is None:
            raise LookupError("business")
        sub = await (await c.execute("SELECT cancel_at_period_end FROM subscriptions WHERE business_id=%s", (business_id,))).fetchone()
        row = await (await c.execute(
            """INSERT INTO deletion_requests (business_id, requested_by, requested_at, purge_after, ai_was_enabled, renews_was)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (business_id) DO NOTHING RETURNING purge_after""",
            (business_id, account_id, now, now + timedelta(days=grace_days), biz["ai_enabled"], bool(sub and sub["cancel_at_period_end"])))).fetchone()
        if row is None:                                                  # already scheduled: the first date stands
            return (await (await c.execute("SELECT purge_after FROM deletion_requests WHERE business_id=%s", (business_id,))).fetchone())["purge_after"]
        await c.execute("UPDATE businesses SET ai_enabled=false WHERE id=%s", (business_id,))
        await c.execute("UPDATE subscriptions SET cancel_at_period_end=true WHERE business_id=%s", (business_id,))
        await platform_audit(c, actor_account_id=account_id, actor_label="owner", action="tenant.deletion_requested", business_id=business_id,
                             detail={"grace_days": grace_days})
        return row["purge_after"]


async def cancel_deletion(db: Database, business_id: uuid.UUID, account_id: uuid.UUID | None) -> bool:
    """Withdraw the request and put the assistant and the renewals back as they were. False when nothing was scheduled."""
    async with db.system_tx() as c:
        req = await (await c.execute("DELETE FROM deletion_requests WHERE business_id=%s RETURNING ai_was_enabled, renews_was", (business_id,))).fetchone()
        if req is None:
            return False
        await c.execute("UPDATE businesses SET ai_enabled=%s WHERE id=%s", (req["ai_was_enabled"], business_id))
        await c.execute("UPDATE subscriptions SET cancel_at_period_end=%s WHERE business_id=%s", (req["renews_was"], business_id))
        await platform_audit(c, actor_account_id=account_id, actor_label="owner", action="tenant.deletion_cancelled", business_id=business_id, detail={})
        return True


async def deletion_date(db: Database, business_id: uuid.UUID) -> datetime | None:
    async with db.system_tx() as c:
        row = await (await c.execute("SELECT purge_after FROM deletion_requests WHERE business_id=%s", (business_id,))).fetchone()
    return row["purge_after"] if row else None


async def purge_due(db: Database, queue: Queue | None, now: datetime | None = None) -> int:
    """Erase every shop whose grace has passed (the scheduler calls this). Each erasure leaves a platform-audit record of ids and counts only."""
    now = now or datetime.now(UTC)
    async with db.system_tx() as c:
        due = [r["business_id"] for r in await (await c.execute(
            "SELECT business_id FROM deletion_requests WHERE purge_after <= %s ORDER BY purge_after LIMIT 50", (now,))).fetchall()]
    for bid in due:
        counts = await delete_tenant(db, bid, queue)
        async with db.system_tx() as c:
            await platform_audit(c, actor_account_id=None, actor_label="scheduler", action="tenant.purged", business_id=bid, detail={"rows": counts})
    return len(due)
