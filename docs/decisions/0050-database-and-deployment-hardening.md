# 0050 Database and deployment hardening

**Status:** accepted · 2026-11-23 · founder: "security is on top"; "nothing unnecessarily has delete permission"; "real and easy deployment differ only in configuration"

## Context
A review of the live catalog (5 login roles, 51 tables, 35 with forced row-level security) found:
- Every service (web, gateway, agent, admin) was given `MIGRATION_DATABASE_URL`, the schema owner's login (it can TRUNCATE, ALTER and DROP everything), although only the migration job uses it. A leak from any public component would have exposed it.
- `app_user` could DELETE from 22 tables and `app_system` from 19. The code deletes from 8 and 14 respectively, and never from the money tables.
- No per-login limits (a runaway query or an open transaction could hold the database), and the `PUBLIC` pseudo-role could connect and use the schema.
- The site and the console were published on every network address by default.

## Decision
1. **The schema owner's login belongs to the migration job and nothing else.** `migration_database_url` is optional in `Settings` and required only for the `migrate` role (and the all-in-one development process). It is removed from the four services' compose files and templates; `setup-db` writes it only into `deploy/db/.env` and strips it from older files; `doctor` reads migration state with the platform login (`GRANT SELECT ON schema_migrations TO app_system`).
2. **DELETE is granted per table, only where code deletes, and every grant has a reason in `_grants.sql`.** Postgres cannot grant "DELETE only with a WHERE", so the bounds are: forced row-level security (the shop role sees one shop's rows), no grant where no code deletes, and a statement timeout. Money records (`invoices`, `payments`, `subscriptions`) cannot be deleted by any login. Cascades from a shop's hard delete do not need the child grant.
3. **Limits per login, set on the role** in `db/bootstrap_roles.sql`: `statement_timeout`, `idle_in_transaction_session_timeout`, `lock_timeout` (the schema owner gets a lock timeout but no statement limit, so a migration never queues behind live traffic yet may run long), `row_security = on` for the two RLS roles, `search_path` pinned to `public, pg_catalog`. `PUBLIC` loses the database and the schema.
4. **Real and easy deployment differ only in configuration.** The same image and compose files serve both. Defaults are the safe values: the site and console publish on `127.0.0.1` unless `WEB_BIND` / `ADMIN_BIND` widen them (tested); `TRUST_PROXY` is a setting. The real path (`stack.ps1`, `stack.sh`) never reads an example file and stops when a service's `.env` is missing. The easy script generates the `.env` files itself from one set of values (generated secrets, command-line values) and then runs the same per-service start.
5. **Founder console sessions record at sign-in whether the network was the founder's usual one** (`admin_sessions.usual_network`, migration 0036). Judged later against "the last sign-in" the attacker's own network would look usual.

## Consequences
- A leak from any public service no longer yields the schema owner's login.
- A code change that needs a new DELETE must add a grant with a reason (a test fails otherwise: the tenant-isolation test now proves the shop role is refused on a table it must not delete from).
- Two tests that deleted rows to set up state were changed to update in place. A break-glass test that needed a second operator was shortened: with one founder (ADR 0047) there is no second operator.
- Not yet done (see `docs/PRODUCTION_READINESS.md`): TLS required to the database in production, `pgaudit` guidance, migration linter and upgrade-with-data test, per-component env and secrets files, invoice retention after a shop is deleted (needs a founder decision).

## How to revisit
Each grant, limit and default is one line in `_grants.sql`, `bootstrap_roles.sql` or a compose file. Loosening any of them needs a reason written next to it.
