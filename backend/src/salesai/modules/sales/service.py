"""Lead stages and deals (FR-SL-1, FR-SL-7)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from salesai.db import Conn, jsonb
from salesai.events.outbox import emit

ORDER = ["new", "exploring", "interested", "negotiating", "ready_to_buy", "won", "lost"]


def next_stage(current: str, proposed: str, *, selling_stopped: bool = False) -> str:
    """The model proposes a stage; code decides. Won is terminal; lost only changes when the customer
    re-engages; the AI never marks a deal won (the owner does)."""
    if current == "won":
        return "won"
    if proposed == "won":
        return current
    if proposed == "lost":
        return "lost"
    if current == "lost":
        return current if selling_stopped else (proposed if ORDER.index(proposed) >= 1 else current)
    return proposed if ORDER.index(proposed) >= ORDER.index(current) else current


async def capture_deal(c: Conn, business_id: uuid.UUID, conversation_id: uuid.UUID, kind: str, items: list[dict[str, Any]],
                       details: dict[str, Any], value: Decimal | None) -> uuid.UUID:
    """A commitment the AI captured (order or visit); the owner confirms it. Idempotent per conversation+kind
    while a pending deal of that kind exists."""
    existing = await (await c.execute(
        "SELECT id FROM deals WHERE conversation_id=%s AND kind=%s AND status='pending'", (conversation_id, kind))).fetchone()
    if existing:
        await c.execute("UPDATE deals SET items=%s, details=%s, value=%s WHERE id=%s", (jsonb(items), jsonb(details), value, existing["id"]))
        return existing["id"]
    row = await (await c.execute(
        "INSERT INTO deals (business_id, conversation_id, kind, items, details, value) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (business_id, conversation_id, kind, jsonb(items), jsonb(details), value))).fetchone()
    await emit(c, "deal.captured", {"deal_id": row["id"], "conversation_id": conversation_id, "kind": kind},
               business_id=business_id, ordering_key=f"biz:{business_id}")
    return row["id"]


async def close_deal(c: Conn, business_id: uuid.UUID, deal_id: uuid.UUID, status: str, actor_account_id: uuid.UUID | None,
                     lost_reason: str | None = None) -> bool:
    """Only the owner marks deals won or lost; the conversation's lead stage follows."""
    if status not in ("won", "lost"):
        raise ValueError("status must be won or lost")
    d = await (await c.execute(
        "UPDATE deals SET status=%s, closed_at=now(), closed_by=%s WHERE id=%s AND status='pending' RETURNING conversation_id",
        (status, actor_account_id, deal_id))).fetchone()
    if not d:
        return False
    await c.execute("UPDATE conversations SET lead_stage=%s, lost_reason=%s WHERE id=%s", (status, lost_reason if status == "lost" else None, d["conversation_id"]))
    await c.execute(
        "INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity, entity_id, detail) VALUES (%s,%s,'owner',%s,'deal',%s,%s)",
        (business_id, actor_account_id, f"deal.{status}", str(deal_id), jsonb({"lost_reason": lost_reason})))
    return True
