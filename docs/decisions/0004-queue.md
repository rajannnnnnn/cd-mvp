# 0004 Queue abstraction with Postgres and Redis backends

**Status:** accepted · 2026-10-04

## Context
All post-acknowledgment work is asynchronous and must be durable, per-key ordered, delayable, supersedable, retried
with backoff and dead-lettered, fair across tenants, observable, and *swappable with the same test suite*.

## Options
Celery/RQ · SQS · Kafka · RabbitMQ · **own small abstraction with two backends**.

## Decision
`salesai.queue.base.Queue` is the only interface producers and consumers use (`publish`, `claim`, `complete`, `fail`,
`defer`, `supersede`, `cancel_superseded`, `reap_expired`, `stats`, dead-letter and purge operations).
- **Postgres backend (default):** `jobs` table, `FOR UPDATE SKIP LOCKED`, advisory locks for per-key ordering and
  per-tenant caps, fair claiming (each tenant's oldest job before anyone's second), leases with a reaper.
  Jobs and state changes can share a transaction; no extra infrastructure at pilot scale.
- **Redis backend:** every transition is one Lua script (atomic), ordering via per-key sorted sets, tenant caps via
  counters, leases in a sorted set, AOF persistence. Time is taken from the Redis server clock.

`tests/queue_suite.py` is the backend-agnostic property suite (durability, ordering under racing workers, delay,
supersession, retry → dead letter → replay, defer, lease recovery, fairness, tenant cap, stats). Both backends pass it,
and the whole conversation pipeline (webhook → relay → turn → delivery → owner loop → API) also passes on Redis in CI
(`TEST_QUEUE_BACKEND=redis`).

## Consequences
- Choosing Redis is configuration (`QUEUE_BACKEND=redis`); no producer or consumer changes.
- Redis is standalone or Sentinel in v1 (scripts touch keys sharing one hash tag); sharding is a later step.
- Backend-specific table peeks in tests are guarded; the shared suite carries the guarantees.

## How to revisit
Add a backend (SQS, Kafka) by implementing `Queue` and passing the suite. Move to Redis when queue depth or claim
latency under the load tests exceeds the Postgres budget.
