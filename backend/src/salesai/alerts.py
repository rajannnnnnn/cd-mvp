"""Raising a platform alert (ADR 0029). Every component raises alerts through here, so the rules live in one place: four
severities, and one open alert per cause with a count instead of one row per event, which is what keeps the founder's inbox
readable. Delivery is `modules/alerting`; this file only records. Alert text never carries customer message content or
floor prices: callers pass the cause in plain words and ids/counts in `detail`."""
from __future__ import annotations

import json
from typing import Any

SEVERITIES = ("low", "medium", "high", "critical")
RANK = {s: i for i, s in enumerate(SEVERITIES)}
_ORDER_SQL = "ARRAY['low','medium','high','critical']"

_RAISE = """
INSERT INTO operator_alerts (business_id, severity, kind, message, detail, dedup_key)
VALUES (%s, %s, %s, %s, %s::jsonb, %s)
ON CONFLICT (kind, (COALESCE(business_id::text, '')), dedup_key) WHERE resolved_at IS NULL
DO UPDATE SET occurrences = operator_alerts.occurrences + 1,
              last_seen_at = now(),
              message = EXCLUDED.message,
              severity = CASE WHEN array_position(@ORDER, EXCLUDED.severity) > array_position(@ORDER, operator_alerts.severity)
                              THEN EXCLUDED.severity ELSE operator_alerts.severity END,
              -- a worse alert than the founder was told about is announced again
              notified_at = CASE WHEN array_position(@ORDER, EXCLUDED.severity) > array_position(@ORDER, operator_alerts.severity)
                                 THEN NULL ELSE operator_alerts.notified_at END,
              notify_attempts = CASE WHEN array_position(@ORDER, EXCLUDED.severity) > array_position(@ORDER, operator_alerts.severity)
                                     THEN 0 ELSE operator_alerts.notify_attempts END
RETURNING id""".replace("@ORDER", _ORDER_SQL)   # a constant, not input: kept out of an f-string so no value can reach the SQL


async def raise_alert(c: Any, severity: str, kind: str, message: str, *, business_id: Any = None,
                      detail: dict[str, Any] | None = None, dedup_key: str = "") -> int:
    """Record that something is wrong; `c` is a system connection. The same cause (kind + shop + `dedup_key`) while an alert
    for it is still open only counts another occurrence and keeps the highest severity. Returns the alert's id."""
    if severity not in RANK:
        raise ValueError(f"severity must be one of {SEVERITIES}, not {severity!r}")
    row = await (await c.execute(_RAISE, (business_id, severity, kind, message, json.dumps(detail or {}), dedup_key))).fetchone()
    return int(row["id"])
