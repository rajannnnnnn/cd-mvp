"""Backend-agnostic queue properties. Run against every Queue implementation (see test_queue_*.py).
The same suite must pass when QUEUE_BACKEND changes (Technical Design: Async processing)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from salesai.queue.base import Defer, Job, JobSpec, NonRetryable
from salesai.queue.worker import HandlerRegistry, StalenessResolver, WorkerRunner


def spec(q: str, **kw) -> JobSpec:
    return JobSpec(queue=q, kind=kw.pop("kind", "k"), payload=kw.pop("payload", {}), **kw)


def in_future(ms: int) -> datetime:
    return datetime.now(UTC) + timedelta(milliseconds=ms)


class QueueSuite:
    """Subclass and provide a `queue` fixture (an instance with a short backoff)."""

    async def test_publish_claim_complete(self, queue, qname):
        assert await queue.publish(spec(qname, payload={"a": 1}))
        [job] = await queue.claim([qname], "w1")
        assert job.spec.payload == {"a": 1} and job.attempts == 1
        assert await queue.claim([qname], "w2") == []          # running jobs are not re-claimed
        await queue.complete(job)
        assert await queue.claim([qname], "w1") == []

    async def test_dedupe_key_publishes_once(self, queue, qname):
        assert await queue.publish(spec(qname, dedupe_key="evt-1"))
        assert not await queue.publish(spec(qname, dedupe_key="evt-1"))
        jobs = await queue.claim([qname], "w", limit=5)
        assert len(jobs) == 1

    async def test_delayed_job_not_claimed_until_due(self, queue, qname):
        await queue.publish(spec(qname, run_at=in_future(400)))
        assert await queue.claim([qname], "w") == []
        await asyncio.sleep(0.6)
        assert len(await queue.claim([qname], "w")) == 1

    async def test_same_ordering_key_never_concurrent(self, queue, qname):
        for i in range(3):
            await queue.publish(spec(qname, ordering_key="conv-1", payload={"i": i}))
        first = await queue.claim([qname], "w1", limit=5)
        assert [j.spec.payload["i"] for j in first] == [0]     # one at a time, in order
        assert await queue.claim([qname], "w2", limit=5) == []
        await queue.complete(first[0])
        second = await queue.claim([qname], "w2", limit=5)
        assert [j.spec.payload["i"] for j in second] == [1]

    async def test_different_keys_run_in_parallel(self, queue, qname):
        for i in range(4):
            await queue.publish(spec(qname, ordering_key=f"c{i}"))
        assert len(await queue.claim([qname], "w", limit=10)) == 4

    async def test_ordering_holds_under_concurrent_workers(self, queue, qname):
        """20 jobs on one key and 20 on others, 8 racing workers: a key never overlaps."""
        active: dict[str, int] = {}
        violations: list[str] = []
        done: list[str] = []
        reg = HandlerRegistry()

        async def h(job: Job) -> None:
            k = job.spec.ordering_key
            active[k] = active.get(k, 0) + 1
            if active[k] > 1:
                violations.append(k)
            await asyncio.sleep(0.01)
            active[k] -= 1
            done.append(job.id)

        reg.add(qname, "k", h)
        for i in range(20):
            await queue.publish(spec(qname, ordering_key="hot"))
            await queue.publish(spec(qname, ordering_key=f"cold-{i}"))
        runners = [WorkerRunner(queue, reg, [qname], worker_id=f"w{i}", concurrency=4, tenant_cap=None)
                   for i in range(2)]
        stop = asyncio.Event()
        tasks = [asyncio.create_task(r.run(stop)) for r in runners]
        for _ in range(200):
            if len(done) >= 40:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.gather(*tasks)
        assert len(done) == 40 and violations == []

    async def test_supersession_discards_stale_job(self, queue, qname):
        versions = {"conversation:c1": 5}
        res = StalenessResolver()

        async def cur(key: str) -> int | None:
            return versions.get(key)

        res.register("conversation", cur)
        ran: list[int] = []
        reg = HandlerRegistry()

        async def h(job: Job) -> None:
            ran.append(job.spec.entity_version)

        reg.add(qname, "k", h)
        await queue.publish(spec(qname, entity_key="conversation:c1", entity_version=4))
        await queue.publish(spec(qname, entity_key="conversation:c1", entity_version=5))
        r = WorkerRunner(queue, reg, [qname], staleness=res, tenant_cap=None)
        await r.run_once()
        assert ran == [5]                                       # the v4 job never ran

    async def test_cancel_superseded_pending(self, queue, qname):
        await queue.publish(spec(qname, entity_key="conversation:x", entity_version=1))
        await queue.publish(spec(qname, entity_key="conversation:x", entity_version=2))
        assert await queue.cancel_superseded("conversation:x", 2) == 1
        jobs = await queue.claim([qname], "w", limit=5)
        assert [j.spec.entity_version for j in jobs] == [2]

    async def test_retry_then_dead_letter_with_error(self, queue, qname):
        reg = HandlerRegistry()
        calls = []

        async def boom(job: Job) -> None:
            calls.append(job.attempts)
            raise RuntimeError("provider exploded")

        reg.add(qname, "k", boom)
        await queue.publish(spec(qname, max_attempts=3))
        r = WorkerRunner(queue, reg, [qname], tenant_cap=None)
        for _ in range(60):
            await r.run_once()
            if len(calls) >= 3:
                break
            await asyncio.sleep(0.05)
        assert calls == [1, 2, 3]
        await asyncio.sleep(0.05)
        [dead] = await queue.dead_letters(qname)
        assert "provider exploded" in dead["last_error"]
        stats = (await queue.stats([qname]))[0]
        assert stats.dead == 1

    async def test_non_retryable_goes_straight_to_dead_letter(self, queue, qname):
        reg = HandlerRegistry()

        async def bad(job: Job) -> None:
            raise NonRetryable("invalid payload")

        reg.add(qname, "k", bad)
        await queue.publish(spec(qname))
        await WorkerRunner(queue, reg, [qname], tenant_cap=None).run_once()
        [dead] = await queue.dead_letters(qname)
        assert "invalid payload" in dead["last_error"]

    async def test_dead_letter_can_be_replayed(self, queue, qname):
        reg = HandlerRegistry()
        ok = []

        async def bad(job: Job) -> None:
            raise NonRetryable("x")

        async def good(job: Job) -> None:
            ok.append(1)

        reg.add(qname, "k", bad)
        await queue.publish(spec(qname))
        r = WorkerRunner(queue, reg, [qname], tenant_cap=None)
        await r.run_once()
        [dead] = await queue.dead_letters(qname)
        reg.add(qname, "k", good)
        assert await queue.replay_dead(qname, dead["id"])
        await r.run_once()
        assert ok == [1] and await queue.dead_letters(qname) == []

    async def test_defer_does_not_consume_attempts(self, queue, qname):
        reg = HandlerRegistry()
        seen = []

        async def h(job: Job) -> None:
            seen.append(job.attempts)
            if len(seen) < 3:
                raise Defer(0.05, "rate limited")

        reg.add(qname, "k", h)
        await queue.publish(spec(qname, max_attempts=2))
        r = WorkerRunner(queue, reg, [qname], tenant_cap=None)
        for _ in range(60):
            await r.run_once()
            if len(seen) >= 3:
                break
            await asyncio.sleep(0.06)
        assert seen == [1, 1, 1]
        assert (await queue.stats([qname]))[0].dead == 0

    async def test_expired_lease_is_recovered(self, queue, qname):
        await queue.publish(spec(qname, max_attempts=3))
        [job] = await queue.claim([qname], "dies", lease_s=1)
        assert await queue.claim([qname], "w2") == []
        await asyncio.sleep(1.3)
        assert await queue.reap_expired() == 1
        [again] = await queue.claim([qname], "w2")
        assert again.attempts == 2 and again.id == job.id

    async def test_job_survives_queue_restart(self, queue, qname, queue_factory):
        await queue.publish(spec(qname, payload={"durable": True}))
        fresh = await queue_factory()                           # a brand-new process / connection
        [job] = await fresh.claim([qname], "w")
        assert job.spec.payload == {"durable": True}

    async def test_fairness_busy_tenant_cannot_starve_others(self, queue, qname):
        busy, quiet = uuid.uuid4(), uuid.uuid4()
        for i in range(30):
            await queue.publish(spec(qname, business_id=busy, ordering_key=f"b{i}"))
        await queue.publish(spec(qname, business_id=quiet, ordering_key="q0"))
        jobs = await queue.claim([qname], "w", limit=2)
        assert quiet in {j.spec.business_id for j in jobs}

    async def test_per_tenant_concurrency_cap(self, queue, qname):
        t = uuid.uuid4()
        for i in range(5):
            await queue.publish(spec(qname, business_id=t, ordering_key=f"c{i}"))
        first = await queue.claim([qname], "w", limit=5, tenant_cap=2)
        assert len(first) == 2
        assert await queue.claim([qname], "w2", limit=5, tenant_cap=2) == []
        await queue.complete(first[0])
        assert len(await queue.claim([qname], "w2", limit=5, tenant_cap=2)) == 1

    async def test_stats_report_depth_age_and_dead(self, queue, qname):
        await queue.publish(spec(qname))
        await queue.publish(spec(qname, run_at=in_future(60_000)))
        await asyncio.sleep(0.05)
        st = (await queue.stats([qname]))[0]
        assert st.pending == 2 and st.due == 1 and st.oldest_due_age_s > 0
