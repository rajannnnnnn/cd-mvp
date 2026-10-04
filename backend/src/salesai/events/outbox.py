"""Transactional outbox: the state change and its event commit together or not at all."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from salesai.db import Conn, jsonb
from salesai.events.catalog import CATALOG


def _plain(d: dict[str, Any]) -> dict[str, Any]:
    import json
    return json.loads(json.dumps(d, default=str))


async def emit(
    conn: Conn, event: str, payload: BaseModel | dict[str, Any], *,
    business_id: uuid.UUID | str | None, ordering_key: str | None = None,
    entity_key: str | None = None, entity_version: int | None = None,
    run_at: datetime | None = None,
) -> uuid.UUID:
    """Write an event to the outbox inside the caller's transaction. Payload is schema-validated."""
    spec = CATALOG[event]
    model = payload if isinstance(payload, BaseModel) else spec.model(**payload)
    if not isinstance(model, spec.model):
        raise TypeError(f"{event} expects {spec.model.__name__}")
    eid = uuid.uuid4()
    await conn.execute(
        """INSERT INTO outbox (id, business_id, event_type, schema_version, ordering_key,
                               entity_key, entity_version, run_at, payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s, COALESCE(%s, now()), %s)""",
        (eid, business_id, event, spec.version, ordering_key, entity_key, entity_version, run_at,
         jsonb(_plain(model.model_dump(mode="json")))),
    )
    return eid
