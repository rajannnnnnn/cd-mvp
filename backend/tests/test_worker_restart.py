"""ADR 0035: the agent can be restarted at any moment and nothing is lost or stuck. A worker keeps its jobs alive with a heartbeat
while it runs them; when it is told to stop it gives unfinished jobs back at once instead of leaving them to a lease; a job
whose lease was lost cannot be finished by the worker that lost it."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from salesai.queue.base import JobSpec
from salesai.queue.postgres import PostgresQueue
from salesai.queue.worker import HandlerRegistry, WorkerRunner


@pytest.fixture
def qname() -> str:
    return f"q-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def queue(db):
    return PostgresQueue(db, backoff_base=0.05)


def runner(queue, qname, handler, **kw) -> WorkerRunner:
    reg = HandlerRegistry()
    reg.add(qname, "k", handler)
    return WorkerRunner(queue, reg, [qname], tenant_cap=None, **kw)


async def test_a_job_longer_than_its_lease_is_kept_alive_by_the_heartbeat(queue, qname):
    done = []

    async def slow(job):
        await asyncio.sleep(2.2)                                  # three times the lease
        done.append(job.attempts)

    await queue.publish(JobSpec(qname, "k"))
    r = runner(queue, qname, slow, lease_s=1, heartbeat_s=0.3, handler_timeout_s=10)
    task = asyncio.create_task(r.run_once())
    await asyncio.sleep(1.8)
    assert await queue.reap_expired() == 0                        # nobody could take it: the lease kept being renewed
    await task
    assert done == [1]                                            # it ran exactly once


async def test_stopping_gives_unfinished_jobs_back_at_once_and_loses_nothing(queue, qname):
    started = asyncio.Event()

    async def never_finishes(job):
        started.set()
        await asyncio.sleep(60)

    await queue.publish(JobSpec(qname, "k", max_attempts=2))
    r = runner(queue, qname, never_finishes, lease_s=30, heartbeat_s=5, handler_timeout_s=90, shutdown_grace_s=0.3)
    stop = asyncio.Event()
    run = asyncio.create_task(r.run(stop))
    await asyncio.wait_for(started.wait(), 5)
    stop.set()
    await asyncio.wait_for(run, 5)                                # returns after the grace, not after 60 s
    [job] = await queue.claim([qname], "after-restart")           # claimable immediately: no waiting for a 30 s lease
    assert job.attempts == 1                                      # and the interrupted run did not use up an attempt
    assert (await queue.stats([qname]))[0].dead == 0


async def test_a_job_that_finishes_inside_the_grace_is_completed_not_released(queue, qname):
    async def quick(job):
        await asyncio.sleep(0.2)

    await queue.publish(JobSpec(qname, "k"))
    r = runner(queue, qname, quick, shutdown_grace_s=3)
    stop = asyncio.Event()
    run = asyncio.create_task(r.run(stop))
    await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(run, 5)
    assert await queue.claim([qname], "w") == []
    assert (await queue.stats([qname]))[0].pending == 0


async def test_a_worker_that_lost_its_lease_stops_its_handler_and_does_not_complete_the_job(queue, qname):
    started, finished = asyncio.Event(), []

    async def slow(job):
        started.set()
        await asyncio.sleep(5)
        finished.append(1)

    await queue.publish(JobSpec(qname, "k"))
    r = runner(queue, qname, slow, lease_s=1, heartbeat_s=0.2, handler_timeout_s=30)
    task = asyncio.create_task(r.run_once())
    await asyncio.wait_for(started.wait(), 3)
    async with queue.db.system_tx() as c:                         # the lease is taken away (a long pause, a network split)
        await c.execute("UPDATE jobs SET lease_until = now() - interval '1 second' WHERE queue=%s", (qname,))
    assert await queue.reap_expired() == 1
    [again] = await queue.claim([qname], "w2", lease_s=30)
    await asyncio.wait_for(task, 5)
    assert finished == []                                         # its handler was cancelled when it noticed
    assert await queue.claim([qname], "w3") == []                 # and the job is still the new owner's
    await queue.complete(again)
