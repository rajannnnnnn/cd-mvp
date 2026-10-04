# 0012 Containers, compose, reverse proxy, demo vs production configuration

**Status:** accepted · 2026-10-04 · the founder asked to build and test, **not deploy**

## Decision
`deploy/docker-compose.yml` runs the topology on one machine: `postgres` (roles created on first start), one-off
`migrate`, `api` and `ingress` (separate processes so webhook latency never depends on the API), `worker`,
`scheduler`, `web` (Caddy: static files, proxy `/api` and `/webhooks`, security headers, long-lived SSE), optional
`redis` and `seed` profiles. `scripts/up.sh` generates fresh secrets on first run and starts everything with one command.
The same images split across machines unchanged. Migrations run as a separate step; old and new workers may overlap.

Demo mode (`APP_ENV=staging`, simulator on, local LLM stand-in) is selected purely by environment variables; production
refuses to start with the simulator, the local LLM or placeholder secrets.

## Consequences
Container images and compose were validated for syntax and packaging (wheel contents, role startup from the installed
package) but **not built or run with a Docker daemon in this environment**; CI builds them (`compose` job) and runs the
browser suite against the running stack (`e2e` job). No cloud deployment was performed.

## How to revisit
For production: managed Postgres (Indian region), container platform of choice, secrets from a store, a Meta
production app; see docs/RUNNING.md "Going to production".
