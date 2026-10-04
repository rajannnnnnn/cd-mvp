"""The SAME backend-agnostic suite, against the Redis backend (Technical Design: 'the same test suite must pass
when the backend is swapped'). Uses TEST_REDIS_URL if set, else starts a throwaway redis-server."""
from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis

from salesai.queue.redis_backend import RedisQueue
from tests.queue_suite import QueueSuite


@pytest.fixture
def qname() -> str:
    return f"q-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def prefix() -> str:
    return f"{{salesai:t{uuid.uuid4().hex[:8]}}}:"


@pytest.fixture
async def queue_factory(redis_url, prefix):
    made: list[RedisQueue] = []

    async def make():
        q = RedisQueue(redis_url, prefix=prefix, backoff_base=0.05)
        made.append(q)
        return q

    yield make
    for q in made:
        await q.close()


@pytest.fixture
async def queue(queue_factory):
    return await queue_factory()


class TestRedisQueue(QueueSuite):
    pass


async def test_purge_business_removes_every_trace(queue, qname):
    from salesai.queue.base import JobSpec
    biz = uuid.uuid4()
    for i in range(3):
        await queue.publish(JobSpec(qname, "k", {"i": i}, business_id=biz, ordering_key=f"k{i}", dedupe_key=f"d{i}"))
    [running] = await queue.claim([qname], "w", limit=1)
    assert await queue.purge_business(biz) == 3
    assert await queue.claim([qname], "w", limit=10) == []
    assert (await queue.stats([qname]))[0].pending == 0 and (await queue.stats([qname]))[0].running == 0
    assert await queue.publish(JobSpec(qname, "k", {}, business_id=biz, dedupe_key="d0"))   # dedupe marker gone too
    assert running.id


async def test_redis_is_a_valid_queue_for_the_relay_contract(redis_url):
    """The outbox relay only needs Queue.publish with a dedupe key (at-least-once publish stays idempotent)."""
    from salesai.queue.base import JobSpec
    r = aioredis.from_url(redis_url, decode_responses=True)
    q = RedisQueue(redis_url, prefix=f"{{salesai:r{uuid.uuid4().hex[:6]}}}:")
    s = JobSpec("rq", "k", {}, dedupe_key="evt:1:rq:k")
    assert await q.publish(s) and not await q.publish(s)
    await q.close()
    await r.aclose()
