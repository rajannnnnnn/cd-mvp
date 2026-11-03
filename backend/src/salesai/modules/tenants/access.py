"""Break-glass access (ADR 0028): how platform staff may read a shop's conversations. Never by default; only under a grant that has
a stated reason and a short time limit, belongs to one operator and one shop, and leaves a line on the platform trail (which outlives
the shop) and one in the shop's own audit view (what the owner sees under "who from our team looked at my data, and why")."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from salesai.db import Database, jsonb
from salesai.modules.tenants.audit import platform_audit

MIN_REASON = 10


async def shop_audit(db: Database, business_id: uuid.UUID, account_id: uuid.UUID, action: str, detail: dict[str, Any]) -> None:
    """The line the shop owner can see. `action` starts with `platform.` so the shop's view can pick these out."""
    async with db.tenant(business_id) as c:
        await c.execute("INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity, detail) VALUES (%s,%s,'platform team',%s,'business',%s)",
                        (business_id, account_id, action, jsonb(detail)))


async def open_grant(db: Database, account_id: uuid.UUID, business_id: uuid.UUID, reason: str, minutes: int) -> dict[str, Any]:
    reason = reason.strip()
    expires = datetime.now(UTC) + timedelta(minutes=minutes)
    async with db.console_tx() as c:
        if not await (await c.execute("SELECT 1 FROM businesses WHERE id=%s", (business_id,))).fetchone():
            raise LookupError("Business not found.")
        g = await (await c.execute("INSERT INTO break_glass_grants (business_id, account_id, reason, expires_at) VALUES (%s,%s,%s,%s) RETURNING id, expires_at",
                                   (business_id, account_id, reason, expires))).fetchone()
        assert g is not None
        await platform_audit(c, actor_account_id=account_id, actor_label="operator", action="break_glass.opened", business_id=business_id,
                             detail={"grant_id": str(g["id"]), "reason": reason, "minutes": minutes})
    await shop_audit(db, business_id, account_id, "platform.access_opened", {"reason": reason, "minutes": minutes})
    return {"id": str(g["id"]), "expires_at": g["expires_at"]}


async def active_grant(db: Database, account_id: uuid.UUID, business_id: uuid.UUID, grant_id: uuid.UUID | None) -> dict[str, Any] | None:
    """The grant, if it is this operator's, for this shop, not ended and not expired. Anything else is None."""
    if grant_id is None:
        return None
    async with db.console_tx() as c:
        return await (await c.execute(
            """SELECT * FROM break_glass_grants WHERE id=%s AND account_id=%s AND business_id=%s AND ended_at IS NULL AND expires_at > now()""",
            (grant_id, account_id, business_id))).fetchone()


async def end_grant(db: Database, account_id: uuid.UUID, grant_id: uuid.UUID) -> bool:
    async with db.console_tx() as c:
        g = await (await c.execute("UPDATE break_glass_grants SET ended_at = now() WHERE id=%s AND account_id=%s AND ended_at IS NULL RETURNING business_id",
                                   (grant_id, account_id))).fetchone()
        if g is None:
            return False
        await platform_audit(c, actor_account_id=account_id, actor_label="operator", action="break_glass.ended", business_id=g["business_id"],
                             detail={"grant_id": str(grant_id)})
    return True


async def list_conversations(db: Database, grant: dict[str, Any]) -> list[dict[str, Any]]:
    async with db.console_tx() as c:
        rows = await (await c.execute(
            """SELECT cv.id, cu.name AS customer_name, cu.wa_id, cv.lead_stage, cv.last_inbound_at, cv.updated_at,
                      (SELECT count(*) FROM messages m WHERE m.conversation_id = cv.id) AS messages
                 FROM conversations cv JOIN customers cu ON cu.business_id = cv.business_id AND cu.id = cv.customer_id
                WHERE cv.business_id=%s ORDER BY cv.updated_at DESC LIMIT 100""", (grant["business_id"],))).fetchall()
        await platform_audit(c, actor_account_id=grant["account_id"], actor_label="operator", action="break_glass.read", business_id=grant["business_id"],
                             detail={"grant_id": str(grant["id"]), "what": "conversation_list", "conversations": len(rows)})
    await shop_audit(db, grant["business_id"], grant["account_id"], "platform.conversation_viewed",
                     {"reason": grant["reason"], "what": "conversation_list", "conversations": len(rows)})
    return [dict(r) for r in rows]


async def read_messages(db: Database, grant: dict[str, Any], conversation_id: uuid.UUID) -> list[dict[str, Any]]:
    async with db.console_tx() as c:
        if not await (await c.execute("SELECT 1 FROM conversations WHERE id=%s AND business_id=%s", (conversation_id, grant["business_id"]))).fetchone():
            raise LookupError("Conversation not found.")
        rows = await (await c.execute(
            "SELECT id, direction, sender, kind, body, status, created_at FROM messages WHERE conversation_id=%s AND business_id=%s ORDER BY created_at, id LIMIT 500",
            (conversation_id, grant["business_id"]))).fetchall()
        await platform_audit(c, actor_account_id=grant["account_id"], actor_label="operator", action="break_glass.read", business_id=grant["business_id"],
                             detail={"grant_id": str(grant["id"]), "conversation_id": str(conversation_id), "messages": len(rows)})
    await shop_audit(db, grant["business_id"], grant["account_id"], "platform.conversation_viewed",
                     {"reason": grant["reason"], "conversation_id": str(conversation_id), "messages": len(rows)})
    return [dict(r) for r in rows]
