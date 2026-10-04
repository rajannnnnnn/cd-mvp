"""Routes unsent operator alerts to every configured sink. Idempotent and safe to run from a leader-elected scheduler:
an alert is marked notified only after at least one sink accepted it; a critical alert that stays unresolved is repeated
every `reminder` interval; a sink that keeps failing is given up on after `max_attempts` so one dead webhook cannot
block the rest. Alert text never carries customer message content or floor prices."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Protocol

from salesai.db import Database
from salesai.modules.channels import ChannelRegistry
from salesai.phone import wa_id

log = logging.getLogger("salesai.alerting")
RANK = {"info": 0, "warning": 1, "critical": 2}
ICON = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}


class Sink(Protocol):
    name: str

    async def send(self, alert: dict[str, Any], text: str) -> None: ...


class WhatsAppSink:
    """Sends to the platform team's own numbers from the platform sender, using the approved `operator_alert` template
    (free-form text would only be allowed inside a 24-hour window)."""
    name = "whatsapp"

    def __init__(self, channels: ChannelRegistry, numbers: list[str]):
        self.channels, self.numbers = channels, numbers

    async def send(self, alert: dict[str, Any], text: str) -> None:       # noqa: ARG002
        ref, channel = self.channels.platform_sender()
        failed = []
        for n in self.numbers:
            res = await channel.send_template(ref, wa_id(n), "operator_alert", "en", [text[:900]])
            if not res.ok:
                failed.append(f"{n}: {res.error}")
        if len(failed) == len(self.numbers):
            raise RuntimeError("; ".join(failed))


def alert_text(alert: dict[str, Any]) -> str:
    who = f" [{alert['business_name']}]" if alert.get("business_name") else ""
    return f"{ICON.get(alert['severity'], '')} {alert['severity'].upper()}{who}: {alert['message']}"


class AlertRouter:
    def __init__(self, db: Database, sinks: list[Sink], *, min_severity: str = "warning", reminder_minutes: int = 60, max_attempts: int = 5):
        self.db, self.sinks = db, sinks
        self.min_rank = RANK[min_severity]
        self.reminder = timedelta(minutes=reminder_minutes)
        self.max_attempts = max_attempts

    async def dispatch(self) -> int:
        """Push everything that is due; returns how many alerts reached at least one sink."""
        if not self.sinks:
            return 0
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT a.*, b.name AS business_name FROM operator_alerts a LEFT JOIN businesses b ON b.id = a.business_id
                   WHERE a.resolved_at IS NULL AND a.notify_attempts < %s
                     AND (a.notified_at IS NULL OR (a.severity = 'critical' AND a.notified_at < now() - %s))
                   ORDER BY a.id LIMIT 50""", (self.max_attempts, self.reminder))).fetchall()
        sent = 0
        for a in rows:
            if RANK[a["severity"]] < self.min_rank:
                continue
            text = alert_text(a)
            errors: list[str] = []
            ok = 0
            for s in self.sinks:
                try:
                    await s.send(a, text)
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{s.name}: {e}"[:200])
                    log.warning("alert sink %s failed: %s", s.name, e)
            async with self.db.system_tx() as c:
                if ok:
                    await c.execute("UPDATE operator_alerts SET notified_at = now(), notify_error = %s WHERE id=%s", ("; ".join(errors) or None, a["id"]))
                    sent += 1
                else:
                    await c.execute("UPDATE operator_alerts SET notify_attempts = notify_attempts + 1, notify_error = %s WHERE id=%s", ("; ".join(errors), a["id"]))
        return sent
