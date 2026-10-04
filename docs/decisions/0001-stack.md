# 0001 Backend and frontend stack

**Status:** accepted · 2026-10-04

## Context
Technical Design requires: strong LLM SDKs and structured output, async I/O, static typing on domain code, an API
contract generated from code, containers, hireable in India. Starting point offered: Python/FastAPI, a TypeScript SPA,
Postgres, Docker.

## Options
- **Python 3.11+ / FastAPI / Pydantic v2 / psycopg 3 (no ORM)** with Postgres 16.
- Node.js (NestJS or Fastify) with TypeScript end to end.
- Go.
- ORM (SQLAlchemy) instead of hand-written SQL.

## Decision
Python, FastAPI, Pydantic v2 and psycopg 3 with explicit SQL; Postgres 16; React + TypeScript + Vite for the frontend.

Why it wins on the criteria (invariants, requirements, seams, simplicity, reversibility, maturity, cost):
- Pydantic models give schema-validated LLM output (INV-2/9 checks), the OpenAPI contract and request validation from one
  definition. The Anthropic SDK and most speech SDKs are first-class in Python.
- Explicit SQL keeps Row-Level Security (`SET LOCAL app.business_id`, four DB roles, SECURITY DEFINER floor functions)
  visible and testable; an ORM would hide exactly the statements INV-1 and INV-5 depend on.
- `psycopg` async pools match the async worker/API model. FastAPI is mature and widely hired for.
- Postgres gives exact decimals, RLS, transactions, `SKIP LOCKED` queues and `LISTEN/NOTIFY` live updates in one product.

## Consequences
- Python's per-core throughput is lower than Go's. The design scales by adding instances (stateless processes), and
  the pricing engine is a pure module that could be ported if it were ever hot.
- No ORM means migrations and row mapping are written by hand; row access is typed as `dict[str, Any]` (see 0010).

## How to revisit
If measured load (NFR-1/2/5 load tests) shows CPU-bound hot paths that scaling out cannot fix, port that module behind
its existing interface. Replace the frontend framework independently: it only shares the generated API contract.
