"""Learning loop, recording step (ADR 0031).

When the owner answers a conversation the AI had handed over, record the answer together with its context, so a later
step can propose the narrowest scope it applies to. The founder's example is the reason context matters: an owner
replying "no refund" may mean no refunds ever, or only on that product, only for that customer, or only because of
something agreed offline. Learning the general rule from one specific reply would make the assistant confidently wrong.

Here nothing is learned and no behaviour changes. Rules this module keeps:
- only owner-authored text is a learning source; the customer's words are kept as context, never as a source;
- per shop only (proposed INV-13): rows are row-level secured, and only this module and tenant export/delete touch
  the table (enforced by tests/test_learning_capture.py);
- recording must never break the owner's reply: it runs in its own savepoint, and a failure is counted (INV-12)."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from salesai.db import Conn, jsonb
from salesai.obs import LEARNING_CAPTURE_FAILED

log = logging.getLogger("salesai.learning")
Source = Literal["business_app", "dashboard"]


async def capture_owner_answer(c: Conn, *, business_id: uuid.UUID, conversation_id: uuid.UUID, owner_text: str | None,
                               source: Source) -> None:
    """Call inside the reply's own transaction, BEFORE the customer's messages are marked answered."""
    if not owner_text or not owner_text.strip():
        return
    try:
        async with c.transaction():                    # a savepoint: a failure here never undoes the reply itself
            await _record(c, business_id, conversation_id, owner_text.strip(), source)
    except Exception:  # noqa: BLE001 - recording is best-effort; the reply must go through regardless
        LEARNING_CAPTURE_FAILED.inc()
        log.exception("could not record the owner's answer for learning (conversation %s)", conversation_id)


async def _record(c: Conn, business_id: uuid.UUID, conversation_id: uuid.UUID, owner_text: str, source: Source) -> None:
    h = await (await c.execute(
        "SELECT id, reason FROM handoffs WHERE conversation_id=%s AND status='open' ORDER BY created_at DESC LIMIT 1",
        (conversation_id,))).fetchone()
    if h is None:                                      # the AI had not handed over: not an answer to something it lacked
        return
    asked = await (await c.execute(
        """SELECT body FROM messages WHERE conversation_id=%s AND direction='in' AND NOT answered AND body IS NOT NULL
           ORDER BY created_at""", (conversation_id,))).fetchall()
    if not asked:                                      # already marked answered: fall back to the last few things they said
        asked = list(reversed(await (await c.execute(
            """SELECT body FROM messages WHERE conversation_id=%s AND direction='in' AND body IS NOT NULL
               ORDER BY created_at DESC LIMIT 3""", (conversation_id,))).fetchall()))
    conv = await (await c.execute("SELECT customer_id, lead_stage FROM conversations WHERE id=%s", (conversation_id,))).fetchone()
    if conv is None or not asked:
        return
    context: dict[str, Any] = {"lead_stage": conv["lead_stage"]}
    await c.execute(
        """INSERT INTO learning_observations (business_id, conversation_id, customer_id, handoff_id, reason,
                                              customer_text, owner_text, source, context)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (business_id, conversation_id, conv["customer_id"], h["id"], h["reason"],
         "\n".join(r["body"] for r in asked), owner_text, source, jsonb(context)))
