"""Backend-agnostic job runner: claim -> supersession check -> handler -> complete / retry / dead-letter.
Handlers must be idempotent (retries and at-least-once delivery are normal)."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable

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
    def __init__(self, queue: Queue, registry: HandlerRegistry, queues: list[str], *,
                 staleness: StalenessResolver | None = None, worker_id: str = "w-0",
                 concurrency: int = 8, tenant_cap: int | None = 2, poll_s: float = 0.05,
                 handler_timeout_s: float = 90.0, lease_s: int = 120):
        self.queue, self.registry, self.queues = queue, registry, queues
        self.staleness = staleness or StalenessResolver()
        self.worker_id, self.concurrency, self.tenant_cap = worker_id, concurrency, tenant_cap
        self.poll_s, self.handler_timeout_s, self.lease_s = poll_s, handler_timeout_s, lease_s
        self._inflight: set[asyncio.Task[None]] = set()

    async def handle(self, job: Job) -> str:
        """Process one claimed job; returns the outcome label."""
        t0 = time.perf_counter()
        q, k = job.spec.queue, job.spec.kind
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
                        await asyncio.wait_for(handler(job), self.handler_timeout_s)
                        await self.queue.complete(job)
                        outcome = "done"
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
                try:
                    await asyncio.wait_for(stop.wait(), self.poll_s * random.uniform(1, 2))  # noqa: S311
                except TimeoutError:
                    pass
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
