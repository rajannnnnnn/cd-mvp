"""Postgres-backed queue: tables + row locking. Same transaction semantics as the rest of the data."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from salesai.db import Database, jsonb
from salesai.queue.base import Job, JobSpec, QueueStats, backoff_s

_CANDIDATES = """
SELECT j.id, j.queue, j.ordering_key, j.business_id
FROM jobs j
WHERE j.status = 'pending' AND j.run_at <= now() AND j.queue = ANY(%(queues)s)
  AND (j.ordering_key IS NULL OR NOT EXISTS (
        SELECT 1 FROM jobs e
        WHERE e.queue = j.queue AND e.ordering_key = j.ordering_key AND e.status = 'pending'
          AND (e.run_at, e.id) < (j.run_at, j.id)))
ORDER BY j.run_at, j.id
LIMIT 400
"""


def _to_job(r: dict[str, Any]) -> Job:
    spec = JobSpec(
        queue=r["queue"], kind=r["kind"], payload=r["payload"], business_id=r["business_id"],
        ordering_key=r["ordering_key"], run_at=r["run_at"], entity_key=r["entity_key"],
        entity_version=r["entity_version"], dedupe_key=r["dedupe_key"], max_attempts=r["max_attempts"],
    )
    return Job(id=str(r["id"]), spec=spec, attempts=r["attempts"])


class PostgresQueue:
    def __init__(self, db: Database, backoff_base: float = 2.0):
        self.db = db
        self.backoff_base = backoff_base

    async def publish(self, spec: JobSpec) -> bool:
        async with self.db.system_tx() as c:
            return await self.publish_in(c, spec)

    @staticmethod
    async def publish_in(c: Any, spec: JobSpec) -> bool:
        """Publish inside an existing system transaction (used by the outbox relay)."""
        row = await (await c.execute(
            """INSERT INTO jobs (queue, kind, business_id, ordering_key, entity_key, entity_version,
                                 dedupe_key, payload, run_at, max_attempts)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s, COALESCE(%s, now()), %s)
               ON CONFLICT (queue, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
               RETURNING id""",
            (spec.queue, spec.kind, spec.business_id, spec.ordering_key, spec.entity_key,
             spec.entity_version, spec.dedupe_key, jsonb(spec.payload), spec.run_at, spec.max_attempts),
        )).fetchone()
        return row is not None

    async def claim(self, queues: list[str], worker_id: str, *, lease_s: int = 120,
                    limit: int = 1, tenant_cap: int | None = None) -> list[Job]:
        claimed: list[Job] = []
        async with self.db.system_tx() as c:
            cands = await (await c.execute(_CANDIDATES, {"queues": queues})).fetchall()
            # fairness: take each tenant's oldest job before anyone's second job
            seen: dict[Any, int] = {}
            ranked = []
            for r in cands:
                seen[r["business_id"]] = seen.get(r["business_id"], 0) + 1
                ranked.append((seen[r["business_id"]], r))
            ranked.sort(key=lambda t: t[0])
            for _, r in ranked:
                if len(claimed) >= limit:
                    break
                if r["ordering_key"] is not None:
                    got = await (await c.execute(
                        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS ok",
                        (f"k:{r['queue']}:{r['ordering_key']}",))).fetchone()
                    if not got["ok"]:
                        continue
                    busy = await (await c.execute(
                        "SELECT 1 FROM jobs WHERE queue=%s AND ordering_key=%s AND status='running' LIMIT 1",
                        (r["queue"], r["ordering_key"]))).fetchone()
                    if busy:
                        continue
                if tenant_cap is not None and r["business_id"] is not None:
                    got = await (await c.execute(
                        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS ok",
                        (f"t:{r['queue']}:{r['business_id']}",))).fetchone()
                    if not got["ok"]:
                        continue
                    n = await (await c.execute(
                        "SELECT count(*) AS n FROM jobs WHERE queue=%s AND business_id=%s AND status='running'",
                        (r["queue"], r["business_id"]))).fetchone()
                    if n["n"] >= tenant_cap:
                        continue
                row = await (await c.execute(
                    """UPDATE jobs SET status='running', attempts=attempts+1, locked_by=%s,
                              lease_until = now() + make_interval(secs => %s)
                       WHERE id = (SELECT id FROM jobs WHERE id=%s AND status='pending' FOR UPDATE SKIP LOCKED)
                       RETURNING *""", (worker_id, lease_s, r["id"]))).fetchone()
                if row:
                    claimed.append(_to_job(row))
        return claimed

    async def complete(self, job: Job) -> None:
        async with self.db.system_tx() as c:
            await c.execute("UPDATE jobs SET status='done', finished_at=now(), lease_until=NULL WHERE id=%s",
                            (int(job.id),))

    async def fail(self, job: Job, error: str, *, retryable: bool = True) -> str:
        dead = (not retryable) or job.attempts >= job.spec.max_attempts
        async with self.db.system_tx() as c:
            if dead:
                await c.execute(
                    "UPDATE jobs SET status='dead', last_error=%s, finished_at=now(), lease_until=NULL WHERE id=%s",
                    (error[:2000], int(job.id)))
            else:
                await c.execute(
                    """UPDATE jobs SET status='pending', last_error=%s, lease_until=NULL, locked_by=NULL,
                              run_at = now() + make_interval(secs => %s) WHERE id=%s""",
                    (error[:2000], backoff_s(job.attempts, base=self.backoff_base), int(job.id)))
        return "dead" if dead else "retry"

    async def defer(self, job: Job, delay_s: float) -> None:
        async with self.db.system_tx() as c:
            await c.execute(
                """UPDATE jobs SET status='pending', attempts=GREATEST(attempts-1,0), lease_until=NULL, locked_by=NULL,
                          run_at = now() + make_interval(secs => %s) WHERE id=%s""", (delay_s, int(job.id)))

    async def supersede(self, job: Job) -> None:
        async with self.db.system_tx() as c:
            await c.execute("UPDATE jobs SET status='cancelled', finished_at=now(), lease_until=NULL WHERE id=%s",
                            (int(job.id),))

    async def cancel_superseded(self, entity_key: str, below_version: int) -> int:
        async with self.db.system_tx() as c:
            r = await c.execute(
                """UPDATE jobs SET status='cancelled', finished_at=now()
                   WHERE entity_key=%s AND status='pending' AND entity_version < %s""", (entity_key, below_version))
            return r.rowcount

    async def reap_expired(self) -> int:
        async with self.db.system_tx() as c:
            r = await c.execute(
                """UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                          last_error = 'lease expired (worker died?)', lease_until=NULL, locked_by=NULL,
                          finished_at = CASE WHEN attempts >= max_attempts THEN now() END
                   WHERE status='running' AND lease_until < now()""")
            return r.rowcount

    async def stats(self, queues: list[str]) -> list[QueueStats]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT queue,
                          count(*) FILTER (WHERE status='pending') AS pending,
                          count(*) FILTER (WHERE status='pending' AND run_at <= now()) AS due,
                          count(*) FILTER (WHERE status='running') AS running,
                          count(*) FILTER (WHERE status='dead') AS dead,
                          COALESCE(EXTRACT(EPOCH FROM now() - min(run_at) FILTER (WHERE status='pending' AND run_at <= now())), 0) AS age
                   FROM jobs WHERE queue = ANY(%s) AND status IN ('pending','running','dead') GROUP BY queue""",
                (queues,))).fetchall()
        by = {r["queue"]: r for r in rows}
        out = []
        for q in queues:
            r = by.get(q)
            out.append(QueueStats(q, r["pending"], r["due"], r["running"], r["dead"], float(r["age"])) if r else QueueStats(q))
        return out

    async def dead_letters(self, queue: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self.db.system_tx() as c:
            return await (await c.execute(
                """SELECT id, kind, business_id, payload, attempts, last_error, finished_at
                   FROM jobs WHERE queue=%s AND status='dead' ORDER BY finished_at DESC LIMIT %s""",
                (queue, limit))).fetchall()

    async def replay_dead(self, queue: str, job_id: str) -> bool:
        async with self.db.system_tx() as c:
            r = await c.execute(
                """UPDATE jobs SET status='pending', attempts=0, run_at=now(), finished_at=NULL, last_error=NULL
                   WHERE id=%s AND queue=%s AND status='dead'""", (int(job_id), queue))
            return r.rowcount > 0

    async def purge_business(self, business_id: uuid.UUID) -> int:
        async with self.db.system_tx() as c:
            return (await c.execute("DELETE FROM jobs WHERE business_id=%s", (business_id,))).rowcount

    async def purge_finished(self, older_than: timedelta) -> int:
        async with self.db.system_tx() as c:
            return (await c.execute(
                "DELETE FROM jobs WHERE status IN ('done','cancelled') AND finished_at < now() - make_interval(secs => %s)",
                (older_than.total_seconds(),))).rowcount


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_worker_id() -> str:
    return f"w-{uuid.uuid4().hex[:8]}"
