"""Webhook ingress. The request path does exactly: verify signature -> store raw event -> enqueue
(outbox) -> acknowledge. No LLM, no Meta, no business logic (NFR-1, INV-6)."""
from __future__ import annotations

import json
import logging
import time

from salesai.db import Database, jsonb
from salesai.events.outbox import emit
from salesai.modules.channels.whatsapp import verify_signature
from salesai.obs import WEBHOOK_SECONDS

log = logging.getLogger("salesai.ingress")
MAX_BODY = 1_000_000


async def accept_webhook(db: Database, app_secret: str, raw_body: bytes, signature: str | None) -> tuple[int, dict]:
    t0 = time.perf_counter()
    try:
        if len(raw_body) > MAX_BODY:
            return 413, {"error": "payload too large"}
        if not verify_signature(app_secret, raw_body, signature):
            log.warning("webhook rejected: bad signature")
            return 401, {"error": "invalid signature"}
        try:
            payload = json.loads(raw_body)
        except ValueError:
            return 400, {"error": "invalid json"}
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            return 400, {"error": "unexpected payload"}
        async with db.system_tx() as c:
            row = await (await c.execute(
                "INSERT INTO webhook_events (signature_valid, payload) VALUES (true, %s) RETURNING id", (jsonb(payload),))).fetchone()
            await emit(c, "webhook.received", {"webhook_event_id": row["id"]}, business_id=None)
        return 200, {"ok": True}
    finally:
        WEBHOOK_SECONDS.observe(time.perf_counter() - t0)


def verify_handshake(verify_token: str, mode: str | None, token: str | None, challenge: str | None) -> str | None:
    """GET subscription handshake: echo hub.challenge when the verify token matches."""
    import hmac
    if mode == "subscribe" and token and hmac.compare_digest(token, verify_token):
        return challenge
    return None
