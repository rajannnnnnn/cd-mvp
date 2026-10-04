# 0002 Modular monolith, process roles and CI-enforced boundaries

**Status:** accepted · 2026-10-04

## Context
"One backend build artifact runs in different roles", "modules with explicit boundaries", "boundaries enforced
automatically in CI", and every seam in *Built for change* must stay intact.

## Options
Microservices from day one · one process with no internal rules · **modular monolith with enforced rules**.

## Decision
One Python package (`salesai`) and one container image. `python -m salesai <role>` starts `api`, `ingress` (webhook
receiver), `web` (both), `worker`, `scheduler`, `migrate` or `all`. Domain code lives in `salesai.modules.*`
(tenants, catalog, pricing, sales, handoffs, channels, auth, delivery, agent, conversations, notifications).

`backend/scripts/check_boundaries.py` (tested in `tests/test_boundaries.py`, run in CI) enforces:
B1 declared module dependency graph · B2 cross-module imports go through the package root (or listed public
submodules) · B3 provider-specific channel code stays inside `channels` · B4 each vendor SDK is imported by one adapter ·
B5 only the composition root names a queue backend · B6 the pricing engine imports only the standard library ·
B7 floor tables and the pricing connection pool are reachable only from an allow-list (INV-1) · B8 dependencies point inward.

## Consequences
- Splitting a hot module into a service is a boundary swap: modules already talk through interfaces and events.
- A new dependency edge is a one-line, reviewed change to the table, not an accident.

## How to revisit
When a module needs its own scaling profile, extract it behind its package-root interface; update the table.
