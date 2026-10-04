"""Handoffs (FR-HO-1) and knowledge gaps (FR-HO-2). Functions take the caller's tenant connection so state
changes and their events commit together (transactional outbox)."""
from __future__ import annotations

import uuid
from datetime import timedelta

from salesai.db import Conn, jsonb
from salesai.events.outbox import emit

HANDOFF_REASONS = ("unknown_answer", "on_request_price", "below_floor", "customer_asked_human", "complaint",
                   "high_value", "system_failure", "other")


async def create_handoff(c: Conn, business_id: uuid.UUID, conversation_id: uuid.UUID, reason: str, note: str | None = None,
                         *, pause_minutes: int | None = 120) -> uuid.UUID:
    """One open handoff per conversation and reason. Pauses the AI in that conversation until the owner
    resolves it or replies (INV-10); the owner is alerted through the outbox."""
    if reason not in HANDOFF_REASONS:
        reason = "other"
    existing = await (await c.execute(
        "SELECT id FROM handoffs WHERE conversation_id=%s AND reason=%s AND status='open'", (conversation_id, reason))).fetchone()
    if existing:
        return existing["id"]
    row = await (await c.execute(
        "INSERT INTO handoffs (business_id, conversation_id, reason, note) VALUES (%s,%s,%s,%s) RETURNING id",
        (business_id, conversation_id, reason, note))).fetchone()
    if pause_minutes:
        await c.execute(
            "UPDATE conversations SET ai_paused_until = now() + %s, ai_paused_reason = 'handoff' WHERE id=%s",
            (timedelta(minutes=pause_minutes), conversation_id))
    await emit(c, "handoff.created", {"handoff_id": row["id"], "conversation_id": conversation_id, "reason": reason},
               business_id=business_id, ordering_key=f"biz:{business_id}")
    return row["id"]


async def resolve_handoff(c: Conn, business_id: uuid.UUID, handoff_id: uuid.UUID, actor_account_id: uuid.UUID | None) -> bool:
    h = await (await c.execute(
        "UPDATE handoffs SET status='resolved', resolved_at=now(), resolved_by=%s WHERE id=%s AND status='open' RETURNING conversation_id",
        (actor_account_id, handoff_id))).fetchone()
    if not h:
        return False
    still_open = await (await c.execute(
        "SELECT 1 FROM handoffs WHERE conversation_id=%s AND status='open'", (h["conversation_id"],))).fetchone()
    if not still_open:   # release the AI pause that the handoff set (not one set by an owner reply)
        await c.execute(
            "UPDATE conversations SET ai_paused_until=NULL, ai_paused_reason=NULL WHERE id=%s AND ai_paused_reason='handoff'",
            (h["conversation_id"],))
    return True


async def log_gap(c: Conn, business_id: uuid.UUID, conversation_id: uuid.UUID | None, question: str) -> uuid.UUID:
    q = " ".join(question.split())[:500]
    existing = await (await c.execute(
        "SELECT id FROM knowledge_gaps WHERE status='open' AND lower(question)=lower(%s)", (q,))).fetchone()
    if existing:
        return existing["id"]
    row = await (await c.execute(
        "INSERT INTO knowledge_gaps (business_id, conversation_id, question) VALUES (%s,%s,%s) RETURNING id",
        (business_id, conversation_id, q))).fetchone()
    await emit(c, "knowledge_gap.logged", {"gap_id": row["id"]}, business_id=business_id)
    return row["id"]


async def answer_gap(c: Conn, business_id: uuid.UUID, gap_id: uuid.UUID, answer: str, actor_account_id: uuid.UUID | None,
                     *, confirmed: bool) -> bool:
    """The owner's answer becomes a business fact ONLY after confirmation (FR-HO-2)."""
    if not confirmed:
        raise ValueError("owner confirmation is required before an answer becomes a business fact")
    gap = await (await c.execute(
        "UPDATE knowledge_gaps SET answer=%s, status='answered', answered_at=now() WHERE id=%s AND status='open' RETURNING question",
        (answer.strip(), gap_id))).fetchone()
    if not gap:
        return False
    fact = f"Q: {gap['question']} A: {answer.strip()}"
    await c.execute(
        """UPDATE businesses SET profile = jsonb_set(profile, '{facts}', COALESCE(profile->'facts','[]'::jsonb) || %s::jsonb)
           WHERE id=%s""", (jsonb([fact]), business_id))
    await c.execute(
        "INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity, entity_id) VALUES (%s,%s,'owner','knowledge.answered','knowledge_gap',%s)",
        (business_id, actor_account_id, str(gap_id)))
    return True

