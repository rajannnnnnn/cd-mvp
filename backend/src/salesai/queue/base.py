"""Queue abstraction (HLD: Queuing and async processing). Producers and consumers depend only on
this module. Backends (Postgres, Redis Streams) implement `Queue` and must pass the same
backend-agnostic test suite (tests/queue_suite.py).

Required properties: durable, per-key ordering, delayed execution, supersession, retries with
backoff then dead-letter, idempotent publish (dedupe key), per-tenant fairness, observability."""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class JobSpec:
    queue: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    business_id: uuid.UUID | None = None
    ordering_key: str | None = None          # jobs with the same key never run concurrently
    run_at: datetime | None = None           # None = now; delayed jobs never block a worker
    entity_key: str | None = None            # supersession scope, e.g. "conversation:<id>"
    entity_version: int | None = None        # job is stale if the entity's version moved past it
    dedupe_key: str | None = None            # publishing the same key twice enqueues once
    max_attempts: int = 5


@dataclass
class Job:
    id: str
    spec: JobSpec
    attempts: int


@dataclass
class QueueStats:
    queue: str
    pending: int = 0
    due: int = 0
    running: int = 0
    dead: int = 0
    oldest_due_age_s: float = 0.0


class NonRetryable(Exception):
    """Handler failure that must go straight to the dead-letter queue."""


class Defer(Exception):  # noqa: N818
    """Handler asks to run again later (e.g. rate limit); does not consume an attempt."""

    def __init__(self, delay_s: float, reason: str = "deferred"):
        super().__init__(reason)
        self.delay_s = delay_s


class Queue(Protocol):
    async def publish(self, spec: JobSpec) -> bool:
        """Durably enqueue. Returns False when deduplicated."""

    async def claim(self, queues: list[str], worker_id: str, *, lease_s: int = 120,
                    limit: int = 1, tenant_cap: int | None = None) -> list[Job]:
        """Claim due jobs. Never returns two jobs with the same ordering key at once, never
        a job whose ordering key is running, and spreads claims across tenants (fairness)."""

    async def complete(self, job: Job) -> None: ...

    async def fail(self, job: Job, error: str, *, retryable: bool = True) -> str:
        """Retry with backoff, or dead-letter. Returns 'retry' | 'dead'."""

    async def defer(self, job: Job, delay_s: float) -> None: ...

    async def supersede(self, job: Job) -> None:
        """Discard a claimed job whose conversation moved on (INV-7)."""

    async def cancel_superseded(self, entity_key: str, below_version: int) -> int: ...

    async def reap_expired(self) -> int:
        """Return jobs whose worker died (lease expired) to the queue (or dead-letter them)."""

    async def stats(self, queues: list[str]) -> list[QueueStats]: ...

    async def dead_letters(self, queue: str, limit: int = 50) -> list[dict[str, Any]]: ...

    async def replay_dead(self, queue: str, job_id: str) -> bool: ...


def backoff_s(attempts: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Exponential backoff with jitter. attempts is 1 for the first failure."""
    raw = min(cap, base * (2 ** max(0, attempts - 1)))
    return raw * random.uniform(0.5, 1.0)  # noqa: S311
