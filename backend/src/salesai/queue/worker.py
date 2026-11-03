"""Backend-agnostic job runner: claim -> supersession check -> handler -> complete / retry / dead-letter.
Handlers must be idempotent (retries and at-least-once delivery are normal)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from salesai.obs import JOB_SECONDS, JOBS_TOTAL, bind
from salesai.queue.base import Defer, Job, NonRetryable, Queue

log = logging.getLogger("salesai.worker")
Handler = Callable[[Job], Awaitable[None]]
VersionFn = Callable[[str], Awaitable[int | None]]


class HandlerRegistry:
    def __init__(self) -> None:
        self._h: dict[tuple[str, str], Handler] = {}

    def register(self, queue: str, kind: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._h[(queue, kind)] = fn
            return fn
        return deco

    def add(self, queue: str, kind: str, fn: Handler) -> None:
        self._h[(queue, kind)] = fn

    def get(self, queue: str, kind: str) -> Handler | None:
        return self._h.get((queue, kind))

    def update(self, other: HandlerRegistry) -> None:
        self._h.update(other._h)


class StalenessResolver:
    """Maps an entity key prefix (e.g. 'conversation') to a function returning its current version."""

    def __init__(self) -> None:
        self._fns: dict[str, VersionFn] = {}

    def register(self, prefix: str, fn: VersionFn) -> None:
        self._fns[prefix] = fn

    async def is_stale(self, job: Job) -> bool:
        key, ver = job.spec.entity_key, job.spec.entity_version
        if key is None or ver is None:
            return False
        fn = self._fns.get(key.split(":", 1)[0])
        if fn is None:
            return False
        cur = await fn(key)
        return cur is not None and cur > ver


class WorkerRunner:
    """Restart tolerance (ADR 0035): a short lease that the worker renews with a heartbeat while it runs a job, so a crashed worker's
    jobs come back within seconds and a slow-but-alive worker's never do; on a stop request, jobs not finished inside the grace
    period are given back at once without using up an attempt; and a worker that discovers it lost a job stops working on it."""

    def __init__(self, queue: Queue, registry: HandlerRegistry, queues: list[str], *,
                 staleness: StalenessResolver | None = None, worker_id: str = "w-0",
                 concurrency: int = 8, tenant_cap: int | None = 2, poll_s: float = 0.05,
                 handler_timeout_s: float = 90.0, lease_s: int = 30, heartbeat_s: float | None = None,
                 shutdown_grace_s: float = 20.0):
        self.queue, self.registry, self.queues = queue, registry, queues
        self.staleness = staleness or StalenessResolver()
        self.worker_id, self.concurrency, self.tenant_cap = worker_id, concurrency, tenant_cap
        self.poll_s, self.handler_timeout_s, self.lease_s = poll_s, handler_timeout_s, lease_s
        self.heartbeat_s = heartbeat_s if heartbeat_s is not None else max(0.2, lease_s / 3)
        self.shutdown_grace_s = shutdown_grace_s
        self._inflight: set[asyncio.Task[Any]] = set()
        self._releasing = False

    async def _heartbeat(self, job: Job, work: asyncio.Future[Any], lost: asyncio.Event) -> None:
        """Renew the lease while the handler runs. If the job is no longer ours, stop the handler: its result must not be applied."""
        while True:
            await asyncio.sleep(self.heartbeat_s)
            try:
                ok = await self.queue.extend_lease(job, self.lease_s)
            except Exception:  # noqa: BLE001 - a missed beat is survivable; the next one may land
                log.warning("lease renewal failed for job %s", job.id, exc_info=True)
                continue
            if not ok:
                lost.set()
                work.cancel()
                return

    async def handle(self, job: Job) -> str:
        """Process one claimed job; returns the outcome label."""
        t0 = time.perf_counter()
        q, k = job.spec.queue, job.spec.kind
        lost = asyncio.Event()                     # set by the heartbeat when this worker no longer owns the job
        with bind(business_id=job.spec.business_id, job_id=job.id):
            try:
                if await self.staleness.is_stale(job):
                    await self.queue.supersede(job)
                    outcome = "superseded"
                else:
                    handler = self.registry.get(q, k)
                    if handler is None:
                        await self.queue.fail(job, f"no handler for {q}/{k}", retryable=False)
                        outcome = "no_handler"
                    else:
                        work = asyncio.ensure_future(handler(job))
                        beat = asyncio.create_task(self._heartbeat(job, work, lost))
                        try:
                            await asyncio.wait_for(work, self.handler_timeout_s)
                        finally:
                            beat.cancel()
                        await self.queue.complete(job)
                        outcome = "done"
            except asyncio.CancelledError:
                if self._releasing:                    # told to stop: give the job back now, for free, and let the next worker take it
                    await self.queue.defer(job, 0)
                    outcome = "released"
                elif lost.is_set():
                    outcome = "lost"                   # someone else owns it now; do nothing to it
                    log.warning("job %s: lease lost, handler stopped", job.id)
                else:
                    raise
            except Defer as d:
                await self.queue.defer(job, d.delay_s)
                outcome = "deferred"
            except NonRetryable as e:
                await self.queue.fail(job, f"{type(e).__name__}: {e}", retryable=False)
                outcome = "dead"
                log.error("job dead-lettered (non-retryable): %s", e)
            except Exception as e:  # noqa: BLE001 - handler errors are data, never crash the worker
                res = await self.queue.fail(job, f"{type(e).__name__}: {e}")
                outcome = "dead" if res == "dead" else "retry"
                log.warning("job failed (%s): %s", outcome, e, exc_info=outcome == "dead")
        JOBS_TOTAL.labels(q, k, outcome).inc()
        JOB_SECONDS.labels(q, k).observe(time.perf_counter() - t0)
        return outcome

    async def run_once(self, limit: int | None = None) -> int:
        """Claim and fully process currently-due jobs (used by tests and one-shot tools)."""
        jobs = await self.queue.claim(self.queues, self.worker_id, lease_s=self.lease_s,
                                      limit=limit or self.concurrency, tenant_cap=self.tenant_cap)
        await asyncio.gather(*(self.handle(j) for j in jobs))
        return len(jobs)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            free = self.concurrency - len(self._inflight)
            claimed: list[Job] = []
            if free > 0:
                try:
                    claimed = await self.queue.claim(self.queues, self.worker_id, lease_s=self.lease_s,
                                                     limit=free, tenant_cap=self.tenant_cap)
                except Exception:  # noqa: BLE001
                    log.exception("claim failed")
            for j in claimed:
                t = asyncio.create_task(self.handle(j))
                self._inflight.add(t)
                t.add_done_callback(self._inflight.discard)
            if not claimed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.poll_s * random.uniform(1, 2))  # noqa: S311
        if self._inflight:
            _, pending = await asyncio.wait(self._inflight, timeout=self.shutdown_grace_s)
            if pending:                                # not finished inside the grace: release them rather than wait for a lease
                self._releasing = True
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
