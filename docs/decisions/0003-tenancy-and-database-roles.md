# 0003 Tenant isolation, database roles and where floors live

**Status:** accepted · 2026-10-04 · covers INV-1, INV-5, INV-11

## Context
Tenant isolation must be enforced by the database, not only by application code; private pricing data must be
physically separated so the code that builds LLM context cannot read it.

## Options
Schema-per-tenant · database-per-tenant · **shared tables with Row-Level Security and composite foreign keys**.

## Decision
Shared tables, every tenant table carries `business_id`; `ENABLE` + `FORCE ROW LEVEL SECURITY` with policies on
`current_setting('app.business_id')`; composite foreign keys (`(business_id, id)`) so a child can never point at another
tenant's parent. Tenant work runs in a transaction that first calls `set_config('app.business_id', …, true)`.

Four login roles (`db/bootstrap_roles.sql`):
- `app_owner` owns the schema, used only by the migration job;
- `app_user` for all tenant work (RLS applies; **no access to `price_floors`**);
- `app_pricing` the only role that can read `price_floors`, used only by the pricing service (boundary rule B7);
- `app_system` (BYPASSRLS) for platform work: webhook tenant resolution, queue, outbox relay, authentication.

Floor prices live in `price_floors`, separate from `pricing_policies`, written only through SECURITY DEFINER functions
(`set_price_floor`, `clear_price_floor`, `price_floor_is_set`). API models carry the floor on input only; outputs expose
`floor_set: bool`. Access tokens are encrypted at rest with per-tenant data keys wrapped by `MASTER_KEY` (INV-11).

## Consequences
- A query written without a tenant filter returns nothing instead of leaking. Tests (`test_tenant_isolation.py`)
  attempt cross-tenant reads and writes on every table and view, as every role.
- The platform role bypasses RLS by design; its use is limited to a small set of modules and reviewed.
- Per-tenant sharding later is possible: every query is already tenant-scoped.

## How to revisit
Move very large tenants to their own database by pointing their connections at it; the domain model does not change.
