# 0010 Lint, types and the boundary checker as CI gates

**Status:** accepted · 2026-10-04

## Decision
CI (`.github/workflows/ci.yml`) runs, for the backend: `ruff check` (E, F, I, B, UP, ASYNC, S, SIM), `mypy` in
`strict` mode, `scripts/check_boundaries.py`, the full pytest suite on the Postgres queue, the async pipeline on the Redis
queue, and an OpenAPI drift check; for the frontend: typecheck, vitest, build, generated-client drift check.

mypy is strict everywhere, with one deliberate relaxation: SQL result rows are `dict[str, Any] | None`, and aggregate
queries always return a row, so `index`/`union-attr` errors and `warn_return_any` are disabled **only** for the
data-access modules listed in `pyproject.toml`. The pure domain code (pricing engine, rules, queue and event contracts,
config, crypto) is fully strict. Where a row must exist, `required(row)` raises `LookupError`, which the worker turns into
a retry / dead letter / operator alert (INV-12), never a `TypeError` on `None`. `salesai.seed` (demo data) is excluded.

## Consequences
A ratchet: new modules are strict by default; shrink the override list as data access is migrated to typed row models.
