"""The platform audit trail (ADR 0030): actions the platform takes on a shop, recorded so they outlive the shop.

A shop's own `audit_log` is erased with the shop under DPDP deletion; this trail is not, so `detail` must carry no
personal data - ids, counts and field names only. Append-only: the platform role may insert and read, never edit."""
from __future__ import annotations

import uuid
from typing import Any

from salesai.db import Conn, jsonb


async def platform_audit(c: Conn, *, actor_account_id: uuid.UUID | None, actor_label: str, action: str,
                         business_id: uuid.UUID | None, detail: dict[str, Any] | None = None) -> None:
    await c.execute(
        "INSERT INTO platform_audit_log (actor_account_id, actor_label, action, business_id, detail) VALUES (%s,%s,%s,%s,%s)",
        (actor_account_id, actor_label, action, business_id, jsonb(detail or {})))
