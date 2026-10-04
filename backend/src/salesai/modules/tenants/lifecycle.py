"""Per-tenant data export and hard delete (NFR-10, DPDP). Export never includes floor prices, tokens or secrets."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from salesai.db import Database

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
    ("audit_log", "SELECT * FROM audit_log"),
]


async def export_tenant(db: Database, business_id: uuid.UUID) -> dict[str, Any]:
    out: dict[str, Any] = {"exported_at": datetime.now(UTC).isoformat(), "note": "Floor prices, access tokens and secrets are never exported."}
    async with db.tenant(business_id) as c:
        for name, sql in EXPORT_TABLES:
            out[name] = await (await c.execute(sql)).fetchall()      # type: ignore[arg-type]
    return json.loads(json.dumps(out, default=str))


async def delete_tenant(db: Database, business_id: uuid.UUID) -> dict[str, int]:
    """Hard delete: the tenant's rows cascade; platform-side traces (raw webhooks, simulator ledger, outbox, orphaned accounts) are purged too."""
    counts: dict[str, int] = {}
    async with db.system_tx() as s:
        nums = await (await s.execute("SELECT id, phone_number_id, display_phone FROM whatsapp_numbers WHERE business_id=%s", (business_id,))).fetchall()
        members = await (await s.execute("SELECT account_id FROM business_users WHERE business_id=%s", (business_id,))).fetchall()
        for n in nums:
            r = await s.execute("DELETE FROM webhook_events WHERE payload::text LIKE %s", (f'%"phone_number_id": "{n["phone_number_id"]}"%',))
            counts["webhook_events"] = counts.get("webhook_events", 0) + r.rowcount
            r = await s.execute("DELETE FROM sim_messages WHERE business_phone=%s", (n["display_phone"],))
            counts["sim_messages"] = counts.get("sim_messages", 0) + r.rowcount
        counts["outbox"] = (await s.execute("DELETE FROM outbox WHERE business_id=%s", (business_id,))).rowcount
        counts["jobs"] = (await s.execute("DELETE FROM jobs WHERE business_id=%s", (business_id,))).rowcount
        if nums:
            await s.execute("DELETE FROM rate_buckets WHERE key = ANY(%s)", ([f"num:{n['id']}" for n in nums],))
        counts["businesses"] = (await s.execute("DELETE FROM businesses WHERE id=%s", (business_id,))).rowcount
        ids = [m["account_id"] for m in members]
        if ids:
            counts["accounts"] = (await s.execute(
                """DELETE FROM accounts a WHERE a.id = ANY(%s) AND a.platform_role IS NULL
                   AND NOT EXISTS (SELECT 1 FROM business_users bu WHERE bu.account_id = a.id)""", (ids,))).rowcount
    return counts
