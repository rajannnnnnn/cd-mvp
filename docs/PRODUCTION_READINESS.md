# Production readiness plan

Written 2026-11-23 from the founder's list. Status per item: **done** (code and tests in this tree, not yet committed), **designed** (decision made here, not built), **open** (needs the founder or a measurement first). Decisions that need an ADR are marked; ADR 0049 (email) is written, 0050 (database and deployment hardening) and 0051 (reply latency) are to be written when built.

## 1. Database roles and permissions (ADR 0050)

Audit of the live catalog (5 login roles, 51 tables, 35 with forced row-level security):

| Finding | Action | Status |
|---|---|---|
| **Every service (web, gateway, agent, admin) carried `MIGRATION_DATABASE_URL`**, the schema owner's login (can TRUNCATE, ALTER, DROP everything), although only the migration job needs it. A leak from any public component would have exposed it. | `migration_database_url` is now optional and required only for the `migrate` role (and the all-in-one dev process); removed from the four services' compose files and templates; `setup-db` writes it only into `deploy/db/.env` and strips it from older files; `doctor` reads migration state with the platform login. | done |
| `app_user` had DELETE on 22 tables; the code deletes from 8 of them (retention and owner edits). `app_system` had DELETE on 19; money tables (`invoices`, `payments`, `subscriptions`) and others never deleted by code. | DELETE narrowed to the tables the code deletes from; money records cannot be deleted by any login; each remaining DELETE grant has a reason in `_grants.sql`. | done |
| "DELETE without WHERE": Postgres cannot grant "only with a WHERE". What bounds it: (1) forced RLS limits `app_user` to one shop's rows, (2) DELETE only where code needs it, (3) statement timeout. A server-level guard (the `safeupdate` extension) is possible on self-managed Postgres but not on most managed ones. | Grants and RLS as above. Add `pg_safeupdate` to the self-managed setup guide as optional. | done / designed |
| No per-login limits: a runaway query or a transaction left open could hold the database. | `statement_timeout`, `idle_in_transaction_session_timeout`, `lock_timeout` set per role in `db/bootstrap_roles.sql`; `search_path` pinned; `row_security = on` for the two RLS roles. | done |
| `PUBLIC` could connect and use the schema by default. | `REVOKE ALL ... FROM PUBLIC` on the database and schema. | done |
| 16 tables have no RLS (platform tables: sessions, jobs, alerts, keys, admin). | Intended: only `app_system` and `app_console` reach them. Keep, and add a test that no tenant-facing role can read them. | designed |
| `invoices`/`payments` rows would be removed by the hard delete of a shop if their foreign keys cascade; Indian tax rules require keeping invoices. | **Open (founder):** keep invoices for N years after a shop is deleted. Needs a decision, then a foreign-key change. | open |
| Remaining "peak security" items | Require TLS to the database (`sslmode=require` in every URL in production, refused otherwise); `pgaudit` for DDL and role changes on self-managed Postgres; password rotation script (setup-db already renews); encrypted backups and a restore drill; never use the superuser from the app. | designed |

## 2. Migrations by version, safe on existing data

What exists: numbered files, one transaction each, `schema_migrations` with checksum, advisory lock, edited-after-applied detection, grants re-applied each run, a written rule "backward compatible with the previous release".

Gaps and the plan (all **designed**, not built):
1. **Expand then contract.** A release may only add (nullable columns, new tables, new indexes `CONCURRENTLY`); removal or rename happens a release later after the code stopped using it. A **migration linter** in CI fails the build on `DROP`, `RENAME`, `SET NOT NULL` without a backfill, `ALTER TYPE`, or an index without `CONCURRENTLY`, unless the file carries an explicit, reviewed `-- destructive: <reason>` marker.
2. **Upgrade-with-data test.** Apply migrations up to N-1, insert a realistic fixture (shops, products, conversations, deals, money rows), apply N, assert nothing was lost or changed. Runs for every new migration.
3. **Safe on a live database.** Migration session sets `lock_timeout` so a migration never queues behind live traffic and blocks it; big backfills run in batches, in a separate step.
4. **Numbering safety.** CI fails on a duplicate or skipped number (two branches both adding the same next number).
5. **Versions on the app side.** The app records the schema version it needs; parts refuse to start (clear message) if the database is older than that, and tolerate newer (backward-compatible by rule 1).
6. **Rollback policy.** Forward-only. A bad migration is fixed by the next migration, restoring from backup being the last resort; the restore drill in section 1 covers it.

## 3. Email: two streams (ADR 0049)

Founder stream **exists**; client stream **designed**. Updated from the founder's later instructions:
- Senders `founder-alert@` and `alerts@` on the same domain (domain to be named by the founder).
- **One mail flow with priorities (founder's amendment).** One outbox and one sender for all mail; founder mail has the highest priority and is always taken first, so client volume can never delay a founder alert. The worker claims rows by priority then age with `SKIP LOCKED`, so it can be scaled out when client mail grows. Client mail is mandatory only in v1 (invoices, renewal reminders, confirmations: no unsubscribe); no daily summary or other optional mail. Sender addresses are env configuration; SES needs the domain verified.
- **Support tickets** go to the founder's address for now, in the same mail stream, but as their own category (`[support]` subject prefix, own alert kind, own recipient setting that defaults to the founder list) so they can be moved to a support inbox without code change. Today they are severity `low` and so wait for the daily digest; they should be delivered promptly (new severity mapping, **to build**).
- **Email must never block anything.** Sending runs outside every customer path (the reply, the webhook, sign-in, sign-up): it is queued, sent by a background job with a connect timeout, retried with backoff, and failure is recorded and alerted through other channels. The alert dispatch currently runs inside the scheduler's slow tick, where a stuck mail server could delay other duties; plan: run it as its own task with a hard timeout (**to build**).
- **Everything critical emails the founder, not only the model.** Present: dead letters, queue stalls, part down (heartbeats), delivery failures, billing events, console security events. **Missing, to add:** model failures (done in this tree), database unreachable or near a connection or disk limit, migration failure, WhatsApp token expiry or shop number disconnected in bulk, webhook signature failures spiking, mail server itself failing (alert through the webhook or WhatsApp sink instead), backup failure. Each gets a severity and a runbook line in the alert text.

## 4. LLM: no silent traps

Done in this tree: model failures raise founder alerts with a specific reason and severity (ADR 0049 table), recovery closes them.

Audit findings and plan:
- The `max_tokens` cut-off the founder hit: a reasoning model spends its budget thinking, so a fixed answer limit silently truncates. Planned: on a `length` finish, **retry once with a larger budget** instead of failing; log the final sizes; sensible per-step budgets in one table; a unit test that a truncated reply retries.
- Check for similar silent traps: JSON mode support per provider, effort levels per step, timeouts versus retries, fallback provider, daily token cap, prompt size growth, model name typos (caught at start-up by a one-call self-test), currency of pricing table for cost caps. Each becomes an item in the start-up self-test (`doctor --llm`: one call per step, reports latency and tokens). **to build**.

## 5. Reply latency: message in to model call (ADR 0051, designed)

How the path works today: inbound message → relay (polls every 100 ms) → turn detection → **end-of-turn predictor** (rules, no model, instant to compute, but it deliberately waits: 1.2 s for a clearly finished message up to 3 s, 2.6 s or more for "partial", 3.5 s to 10 s for open, 12 s ceiling) → planner call → writer call → optional check call → delivery. So most of the delay before the first model call is the deliberate wait, not the plumbing.

Plan, in order of payoff:
1. **Measure first.** A per-stage breakdown (arrival, relay pickup, predictor wait, planner start/end, writer end, check end, first send) from the existing run records, shown as p50/p95. Without it any tuning is a guess.
2. **Speculative planning (no behaviour change).** Start the planner as the message arrives, in parallel with the quiet wait; if the customer writes again the result is discarded (the existing version check already supports this), otherwise the planner time is already spent when the wait ends. Cost: some wasted calls on bursts, kept small by using the light model and a per-conversation cap. **to build**
3. **Light model for light work.** Planner and check run on a small, fast, non-reasoning (or low-effort) model; the writer keeps the stronger model. A tiny model can also settle the predictor's "unsure" class in a few hundred milliseconds instead of waiting 2-3 s. Configuration: separate `LLM_MODEL_*` per step already exists; add `LLM_MODEL_TURN` for the predictor and a documented profile table. **to build**
4. **Shorter quiet times.** Lowering the 1.2 s floor is a **product-behaviour change** (more risk of answering half a thought). Method: replay the labelled corpus (`tests/eot_corpus.py`) at candidate thresholds and show the founder the speed against wrong-early rate before changing the default. **open (founder)**
5. **Plumbing.** Replace the 100 ms relay poll with Postgres LISTEN/NOTIFY wake-ups; confirm queue workers wake on notify; send the first message part before the rest of the reply is paced.
Note: the thinking-token allowance added today to stop truncation adds latency (up to 800 tokens of thinking at the model's speed). Planner effort is set to `low` for that reason; the measurement in step 1 decides the final numbers.

## 6. Environment files

Decided direction (to build with the secrets split, ADR to follow):
- One folder per **service** (compose stack) and, inside it, one env file per **component** (`api`, `site`, `scheduler`, `worker`...): `deploy/<service>/<component>.env` for configuration and `deploy/<service>/<component>.secrets.env` for secrets, each containing only what that component uses. Generated by the setup tool; the `*.example` files are templates and are never loaded by anything. Production may supply secrets from mounted files (`/run/secrets/NAME`) or a secret manager instead of files on disk.
- Reason: a compromised component then holds only its own secrets. The schema-owner login finding in section 1 is exactly the failure this prevents.

## 7. Deployment: easy and real, with a clear gap

Two profiles, one codebase, and the easy one can never be mistaken for the real one:

| | **Easy** (`fresh-start`, one command) | **Real** (production) |
|---|---|---|
| Purpose | A working product on a laptop or test server in minutes | Customers' data and money |
| Config | **Generated**: random passwords and secrets, database container created, ports chosen; values you give on the command line are written into the env files (no hand editing, nothing hard-coded in scripts) | You provide each value; nothing is invented |
| Database | Local container | Managed Postgres with TLS, the real database address |
| Meta, payments | The simulator and test payment provider | Real Meta app, real payment provider |
| Guard | Writes `APP_ENV=dev`. In dev only, the simulator and test payments may be on | `APP_ENV=production`: **the app refuses to start** with the simulator, test payments, placeholder or example values, the local model, a database without TLS, or a missing secret (most of this already exists in `Settings`; add the remaining checks) |
| Safety | Refuses to run against a database or `.env` marked production | `preflight` command checks everything and prints a pass/fail list before anything starts |

To build: a single **bootstrap tool inside the backend image** (`python -m salesai bootstrap --profile easy|production`) that does the work once and identically on every machine, with thin launchers `fresh-start.ps1` and `fresh-start.sh` (and `stack.ps1`/`stack.sh`, `setup-db`) that only check Docker, build the image, call it, and run `docker compose`. That makes PowerShell and bash behave the same because the logic is in one place, and it can be tested. Preflight checks first (Docker running, ports free, disk, internet), safe re-runs, a final smoke test (a simulated customer message gets a model reply), and on any failure a plain message with the exact fix.

## 8. Edge proxy: nginx or not

Today each of the three public services already has its own **Caddy** (web site, admin site), proxying `/api` and `/webhooks` and serving the app, with TLS, security headers and a CSP. Recommendation: **do not add nginx**; keep Caddy (automatic certificates, fewer lines, already written and tested) unless your hosting standard dictates nginx or a cloud load balancer, in which case that sits in front and Caddy stays inside. What production needs at the edge, per component:
- `web/site`: TLS, HSTS, rate limits on `/api` sign-in and sign-up routes, request size limit, gzip (present except HSTS and rate limits).
- `gateway/receive`: public TLS for Meta; **never alter the body** (signature check needs the raw bytes); limit body size; rate limit per source; accept only POST on `/webhooks/*` plus Meta's verify GET.
- `admin/site`: IP allowlist (`ADMIN_ALLOWED_ORIGINS` and IPs exist) and no public indexing.
- Nothing else is published: databases and internal components stay on the private network.
