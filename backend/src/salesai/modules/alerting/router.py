"""Routes platform alerts to the founder (ADR 0029). Idempotent and safe to run from a leader-elected scheduler.

What each severity does:
  critical  at once, to every sink; repeated every `reminder_minutes` until resolved
  high      at once; then at most once per `high_cooldown_minutes`, and only if it happened again meanwhile
  medium    one rollup email per `medium_rollup_minutes`
  low       one digest email per `low_digest_minutes`
Critical and High also get a recovery notice, once, when resolved. An alert counts as told once at least one sink accepted it;
a failing sink is retried up to `max_attempts` so one dead webhook cannot block the rest. Which sinks take which severity is the
sink's own `levels`. The text never carries customer message content or floor prices, and nothing here looks up a subscriber."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Protocol

from salesai.alerts import RANK
from salesai.db import Database
from salesai.modules.alerting.format import alert_text
from salesai.modules.channels import ChannelRegistry
from salesai.phone import wa_id

log = logging.getLogger("salesai.alerting")
_JOIN = "SELECT a.*, b.name AS business_name FROM operator_alerts a LEFT JOIN businesses b ON b.id = a.business_id"


class Sink(Protocol):
    name: str
    levels: frozenset[str]                 # the severities this sink is meant for

    async def send(self, alert: dict[str, Any], text: str) -> None: ...


class WhatsAppSink:
    """Sends to the platform team's own numbers from the platform sender, using the approved `operator_alert` template
    (free-form text would only be allowed inside a 24-hour window). Critical only: it is the loudest channel."""
    name = "whatsapp"
    levels = frozenset({"critical"})

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


class AlertRouter:
    def __init__(self, db: Database, sinks: list[Sink], *, min_severity: str = "low", reminder_minutes: int = 30,
                 high_cooldown_minutes: int = 30, medium_rollup_minutes: int = 60, low_digest_minutes: int = 1440, max_attempts: int = 5):
        self.db, self.sinks = db, sinks
        self.min_rank = RANK[min_severity]
        self.reminder = timedelta(minutes=reminder_minutes)
        self.high_cooldown = timedelta(minutes=high_cooldown_minutes)
        self.every = {"medium": timedelta(minutes=medium_rollup_minutes), "low": timedelta(minutes=low_digest_minutes)}
        self.max_attempts = max_attempts

    def _wanted(self, severities: tuple[str, ...], *, digests: bool = False) -> list[str]:
        """The severities in `severities` that are at or above the floor and that some sink takes."""
        out = []
        for sev in severities:
            if RANK[sev] >= self.min_rank and any(sev in s.levels and (not digests or hasattr(s, "send_digest")) for s in self.sinks):
                out.append(sev)
        return out

    async def dispatch(self) -> int:
        """Push everything that is due; returns how many alerts or digests reached at least one sink."""
        if not self.sinks:
            return 0
        return await self._immediate() + await self._digest("medium") + await self._digest("low") + await self._recoveries()

    # ------------------------------------------------------------------ critical and high: one alert at a time
    async def _immediate(self) -> int:
        sevs = self._wanted(("high", "critical"))
        if not sevs:
            return 0
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                f"""{_JOIN}
                    WHERE a.resolved_at IS NULL AND a.notify_attempts < %s AND a.severity = ANY(%s)
                      AND (a.notified_at IS NULL
                           OR (a.severity = 'critical' AND a.notified_at < now() - %s)
                           OR (a.severity = 'high' AND a.occurrences > a.notified_occurrences AND a.notified_at < now() - %s))
                    ORDER BY a.id LIMIT 50""", (self.max_attempts, sevs, self.reminder, self.high_cooldown))).fetchall()
        sent = 0
        for a in rows:
            ok, errors = await self._fan_out(a, alert_text(a), kind="alert")
            async with self.db.system_tx() as c:
                if ok:
                    await c.execute("""UPDATE operator_alerts SET notified_at = now(), notified_occurrences = %s, notify_attempts = 0,
                                              notify_error = %s WHERE id = %s""", (a["occurrences"], "; ".join(errors) or None, a["id"]))
                    sent += 1
                else:
                    await c.execute("UPDATE operator_alerts SET notify_attempts = notify_attempts + 1, notify_error = %s WHERE id = %s",
                                    ("; ".join(errors), a["id"]))
        return sent

    async def _fan_out(self, alert: dict[str, Any], text: str, kind: str = "alert") -> tuple[bool, list[str]]:
        errors: list[str] = []
        ok = False
        for s in self.sinks:
            if alert["severity"] not in s.levels:
                continue
            try:
                await s.send(alert, text)
                ok = True
                await self._record(s, kind, alert["severity"], alert["id"], True, None)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{s.name}: {e}"[:200])
                log.warning("alert sink %s failed: %s", s.name, e)
                await self._record(s, kind, alert["severity"], alert["id"], False, str(e))
        return ok, errors

    async def _record(self, sink: Any, kind: str, severity: str, alert_id: int | None, ok: bool, error: str | None, items: int = 1) -> None:
        """One row and one log line per attempt, so "did it go out?" has an answer. It never carries the alert's text or an address, and a
        failure to write it never stops the alert."""
        ref = getattr(sink, "last_ref", None) if ok else None
        log.info("alert %s via %s %s%s", kind, sink.name, "sent" if ok else "FAILED", f" (ref {ref})" if ref else "")
        try:
            async with self.db.system_tx() as c:
                await c.execute(
                    "INSERT INTO alert_deliveries (alert_id, kind, severity, sink, ok, provider_ref, error, items) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (alert_id, kind, severity, sink.name, ok, ref, (error or None) and error[:300], items))
        except Exception:  # noqa: BLE001
            log.exception("could not record the delivery")

    # ------------------------------------------------------------------ medium and low: gathered into one email
    async def _digest(self, severity: str) -> int:
        if not self._wanted((severity,), digests=True):
            return 0
        every = self.every[severity]
        async with self.db.system_tx() as c:
            pending = await (await c.execute(
                f"""{_JOIN} WHERE a.resolved_at IS NULL AND a.severity = %s AND (a.notified_at IS NULL OR a.occurrences > a.notified_occurrences)
                    ORDER BY a.id LIMIT 200""", (severity,))).fetchall()
            if not pending:
                return 0
            # due once a full interval has passed since the last digest of this severity (or, if there never was one, since the
            # oldest alert still waiting began): a digest is a gathering, so the first alert of a quiet spell waits its turn
            last = ((await (await c.execute("SELECT max(notified_at) AS t FROM operator_alerts WHERE severity = %s", (severity,))).fetchone()) or {})["t"]
            due = ((await (await c.execute("SELECT %s::timestamptz < now() - %s AS due",
                                           (last or min(a["created_at"] for a in pending), every))).fetchone()) or {})["due"]
        if not due:
            return 0
        errors: list[str] = []
        ok = False
        for s in self.sinks:
            if severity in s.levels and hasattr(s, "send_digest"):
                try:
                    await s.send_digest(severity, pending)
                    ok = True
                    await self._record(s, "digest", severity, None, True, None, len(pending))
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{s.name}: {e}"[:200])
                    log.warning("alert digest via %s failed: %s", s.name, e)
                    await self._record(s, "digest", severity, None, False, str(e), len(pending))
        async with self.db.system_tx() as c:
            ids = [a["id"] for a in pending]
            if ok:
                await c.execute("""UPDATE operator_alerts SET notified_at = now(), notified_occurrences = occurrences, notify_error = NULL
                                   WHERE id = ANY(%s)""", (ids,))
            else:
                await c.execute("UPDATE operator_alerts SET notify_error = %s WHERE id = ANY(%s)", ("; ".join(errors), ids))
        return len(pending) if ok else 0

    # ------------------------------------------------------------------ recovery: close the loop on what was announced
    async def _recoveries(self) -> int:
        sevs = self._wanted(("high", "critical"))
        if not sevs:
            return 0
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                f"""{_JOIN} WHERE a.resolved_at IS NOT NULL AND a.recovery_notified_at IS NULL AND a.notified_at IS NOT NULL
                      AND a.severity = ANY(%s) AND a.resolved_at > now() - interval '1 day' ORDER BY a.id LIMIT 50""", (sevs,))).fetchall()
        sent = 0
        for a in rows:
            ok, _ = await self._fan_out({**a, "recovered": True}, alert_text({**a, "recovered": True}), kind="recovery")
            if ok:
                async with self.db.system_tx() as c:
                    await c.execute("UPDATE operator_alerts SET recovery_notified_at = now() WHERE id = %s", (a["id"],))
                sent += 1
        return sent
