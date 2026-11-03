# 0042 The login that can read price floors goes to the agent's turn worker only

**Status:** accepted · 2026-11-03 · technical decision (INV-1, ADR 0025 "per-component database roles")

## Context

INV-1 says a price floor never leaves the pricing component. The database already enforces half of it: `price_floors` is
readable by one role, `app_pricing`, and the owner writes a floor through functions that run as `app_user` and cannot read
it back. The other half was left to deployment: every process (web API, webhook receiver, gateway, scheduler, console, migrate
job) was given all four database logins, including `PRICING_DATABASE_URL`, so a breach of any of them could read every shop's
floor. The code path that actually reads a floor is one function (`PricingService`, called when the agent decides a turn).

## Options

1. **Leave it; rely on code review** that nothing else calls the pricing service. A weak guard for the most sensitive number in
   the product.
2. **Four more database roles, one per component.** The full answer to ADR 0025's "per-component roles", but it needs new grants
   for every table each component touches and a test for each: a large change with a real risk of breaking a part in production.
3. **Give the floor-reading login to the one process that uses it, and nobody else.** Small, testable now, and it removes the
   sensitive credential from every other process.

## Decision

Option 3. `PRICING_DATABASE_URL` is required only where turns are decided: a worker that consumes `conversation.turns`
(and the all-in-one development process). It is optional everywhere else, and the deploy files give it to `agent/agent`
alone (not even the agent's own scheduler). `Database` opens the pricing pool only when it has the login, and
`pricing_tenant` raises a clear error if a process without it ever tries. Tests: `test_config_roles.py` (who must hold
it, who may go without), `test_deploy_files.py` (only `agent/agent` is given it, and the application agrees about who needs it).

## Consequences

- A breach of the web API, the webhook receiver, the gateway, the scheduler or the founder's console no longer yields the
  floor-reading login. INV-1 now holds at the credential level, not only in the code path.
- The other three logins (`app_user`, `app_system`, `app_owner`) are still shared by several parts. `app_system` bypasses row
  security and is the largest remaining exposure; narrowing it per component (option 2) stays open and is the next step of
  ADR 0025's roles item.
- A part that needs floors in future (for instance a console feature) cannot get them by accident; it must be given the login
  deliberately, with this decision revisited.

## How to revisit

If a second component must read floors, add it to `TURN_QUEUES`/`reads_floors` in `config.py` and to the allow-list in
`test_deploy_files.py`, with a reason. If per-component roles (option 2) are built, supersede this record.
