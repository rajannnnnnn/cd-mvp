# 0043 The founder console gets its own database login

**Status:** accepted · 2026-11-03 · technical decision (ADR 0025 "per-component database roles", ADR 0028)

## Context

`app_system` (bypasses row security) is shared by the web API, webhook receiver, gateway, agent and scheduler, and until now also held the
console's own tables: the authenticator secrets (`admin_credentials`), console sessions and break-glass grants. A breach of any public-facing
part could have read the second factor of every operator and the live console sessions, which defeats the separate sign-in of ADR 0028.

## Options

1. **One role per component** (web, gateway, agent, scheduler, console). The full answer, but each part's table access must be audited and
   proven by tests; a mistake breaks a production path. Large.
2. **A role for the console alone**, taking the console-only tables away from the shared role. Small, testable at the database, and it
   closes the worst exposure.

## Decision

Option 2. New role `app_console` (bypasses row security like `app_system`, and is a member of it so it keeps everything the platform role can
do). Only `app_console` holds `admin_credentials`, `admin_sessions` and `break_glass_grants`; those grants were removed from `app_system`.
`CONSOLE_DATABASE_URL` is required for the console (and the all-in-one development process) and is given to no other deploy part. The console's
sign-in and break-glass code use `Database.console_tx()`. Role creation: `db/bootstrap_roles.sql` (with `-v console_pw=...`, or enabled later with
`ALTER ROLE app_console LOGIN PASSWORD '...'`).

Tests: `test_admin_auth.py` (the shared login is refused on all three tables, the console login can read them and still reads shop data),
`test_config_roles.py` (who must hold it), `test_deploy_files.py` (only `admin/api` is given it).

## Consequences

- Reading an operator's authenticator secret or hijacking a console session now needs the console's own credential, not any public part's.
- Remaining exposure: `app_system` still reads chat text (`messages`) for the agent's recovery sweep and the boards' aggregates. Splitting
  web, gateway, agent and scheduler apart (option 1) is the next step if and when a part is judged exposed enough to justify it.

## How to revisit

Supersede this record when per-component roles for the other parts are built; start from a measured table-access matrix per part.
