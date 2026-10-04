from __future__ import annotations

import uuid

import pytest

from salesai.queue.postgres import PostgresQueue
from tests.queue_suite import QueueSuite


@pytest.fixture
def qname() -> str:
    return f"q-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def queue_factory(db):
    async def make():
        return PostgresQueue(db, backoff_base=0.05)
    return make


@pytest.fixture
async def queue(queue_factory):
    return await queue_factory()


class TestPostgresQueue(QueueSuite):
    pass
