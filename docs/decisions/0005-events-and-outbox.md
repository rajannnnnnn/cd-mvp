# 0005 Versioned event envelope with a transactional outbox

**Status:** accepted · 2026-10-04

## Context
A state change and the event announcing it must both happen or neither. Producers and consumers deploy independently.
New features should subscribe to existing events rather than be wired into the core flow.

## Decision
Producers `emit()` inside the same transaction as their state change; the event is an `outbox` row. A relay
publishes outbox rows to the queue (one instance is enough, more are safe: rows are claimed with SKIP LOCKED and
queue `dedupe_key = <event id>:<queue>:<kind>` makes republishing idempotent). The envelope carries id (dedupe), type,
schema_version, tenant, ordering key, entity key/version and `occurred_at`. `salesai/events/catalog.py` declares every
event as a Pydantic model (`extra="forbid"`, so an undeclared field fails loudly) and its subscribers.

## Consequences
At-least-once delivery with idempotent consumers; ordering per conversation via the ordering key; adding a feature =
declaring a subscriber. The relay adds ~100 ms of latency, well inside NFR budgets.

## How to revisit
Consumers must accept the current and previous `schema_version`; bump it for breaking changes.
