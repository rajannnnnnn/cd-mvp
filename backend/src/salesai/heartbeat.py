"""Knowing when a part is down (ADR 0046).

Every process writes a heartbeat row under a readable unit name; replicas share a unit, so a unit counts as down only when none of
its copies is beating. The schedulers read the rows (`check_parts`) and raise a `part_down` alert for a unit that has gone quiet,
resolving it when the unit beats again. A unit unseen for a day is treated as retired and no longer alerts."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from typing import TYPE_CHECKING, Any

from salesai.alerts import raise_alert

if TYPE_CHECKING:
    from salesai.config import Settings
    from salesai.db import Database

log = logging.getLogger("salesai.heartbeat")

# The parts whose absence leaves customers unanswered: their silence is critical, everything else is high.
CUSTOMER_PATH = frozenset({"gateway receive", "gateway inbound", "gateway send", "agent worker", "all-in-one", "web api and receive"})
RETIRED_AFTER_S = 24 * 3600
_AGENT_QUEUES = {"conversation.events", "conversation.turns"}


def unit_name(role: str, s: Settings) -> str:
    """The name the founder reads in an alert. Workers are named by the queues they serve, as the deploy files run them."""
    if role == "worker":
        q = set(s.queues)
        if q == {"inbound.events"}:
            return "gateway inbound"
        if q == {"outbound.actions"}:
            return "gateway send"
        if q >= _AGENT_QUEUES:
            return "agent worker"
        return "worker " + ",".join(sorted(q))
    if role == "scheduler":
        return f"{s.scheduler_component} scheduler" if s.scheduler_component != "all" else "scheduler"
    return {"api": "web api", "ingress": "gateway receive", "admin": "admin api", "web": "web api and receive", "all": "all-in-one"}.get(role, role)


class Heartbeat:
    def __init__(self, db: Database, unit: str, role: str, every_s: float = 30):
        self.db, self.unit, self.role, self.every_s = db, unit, role, every_s
        self.instance = f"{socket.gethostname()}:{os.getpid()}"

    async def beat(self) -> None:
        async with self.db.system_tx() as c:
            await c.execute(
                """INSERT INTO heartbeats (unit, role, instance) VALUES (%s, %s, %s)
                   ON CONFLICT (unit) DO UPDATE SET last_seen_at = now(), role = EXCLUDED.role, instance = EXCLUDED.instance""",
                (self.unit, self.role, self.instance))

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.beat()
            except Exception:  # noqa: BLE001 - a missed beat is what the watchers are for; never take the process down
                log.warning("heartbeat not recorded for %s", self.unit, exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.every_s)


async def check_parts(db: Database, down_after_s: int) -> dict[str, Any]:
    """Raise part_down for quiet units; resolve it for units that beat again. Safe to run from every scheduler at once."""
    raised: list[str] = []
    resolved = 0
    async with db.system_tx() as c:
        rows = await (await c.execute(
            "SELECT unit, EXTRACT(EPOCH FROM now() - last_seen_at) AS quiet_s FROM heartbeats WHERE last_seen_at > now() - make_interval(secs => %s)",
            (RETIRED_AFTER_S,))).fetchall()
        for r in rows:
            unit, quiet = r["unit"], float(r["quiet_s"])
            if quiet > down_after_s:
                await raise_alert(c, "critical" if unit in CUSTOMER_PATH else "high", "part_down",
                                  f"{unit} has not checked in for {int(quiet // 60)} min {int(quiet % 60)} s",
                                  dedup_key=unit, detail={"unit": unit, "quiet_s": int(quiet)})
                raised.append(unit)
            else:
                cur = await c.execute(
                    "UPDATE operator_alerts SET resolved_at = now() WHERE kind = 'part_down' AND dedup_key = %s AND resolved_at IS NULL", (unit,))
                resolved += cur.rowcount
    return {"down": raised, "resolved": resolved}
