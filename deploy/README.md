# Deploying one part at a time

The product is four parts. Each is its own folder, its own `.env`, and its own docker compose project, so you can clone the
repository, configure only the parts you need, and launch, restart and scale them separately. They share the database and
nothing else: no part calls another, and a rule in CI fails the build if the code ever starts to (ADR 0025).

| Part | Folder | What it is | Holds | Published |
|---|---|---|---|---|
| **db** | `deploy/db` | the database migrations, a one-off job | the schema-owner credential | nothing |
| **web** | `deploy/web` | the site, its REST API, and its own timer (billing, trials, alerts, sign-in data) | `JWT_SECRET`, `OTP_SECRET` | the site, `:8080` |
| **gateway** | `deploy/gateway` | the only part that talks to Meta: `receive`, `inbound`, `send`, its own timer | `META_APP_SECRET`, `META_VERIFY_TOKEN`, `MASTER_KEY`, the platform's WhatsApp token | `receive`, `:8001` |
| **admin** | `deploy/admin` | the founder's console: boards, agent audit, alerts, support, reading a chat as a recorded exception. Its own address and sign-in | `ADMIN_JWT_SECRET`, `MASTER_KEY`, `JWT_SECRET`, `OTP_SECRET` | the console, `:8082` |
| **agent** | `deploy/agent` | the ChatDeal AI: turns, pricing, the writer, owner alerts, follow-ups | the model key | nothing |

Two things to notice. The web part holds no Meta secret and the agent holds no WhatsApp token, so a break-in on either cannot
reach WhatsApp. And the database is **external**: it is never part of a compose file. Point all four `.env` files at it.

## First time

1. Create the database and its roles with `db/bootstrap_roles.sql` (pass `-v console_pw=...` to give the console its own login, `app_console`).
2. For each part you want, copy `.env.example` to `.env` in its folder and fill it in. A value that must be the same in several
   parts says so (`MASTER_KEY`: admin and gateway). Database logins: every part has `DATABASE_URL`, `SYSTEM_DATABASE_URL` and `MIGRATION_DATABASE_URL`; only the agent also has
   `PRICING_DATABASE_URL` (the login that can read price floors) and only the console has `CONSOLE_DATABASE_URL` (ADR 0042, 0043).
3. Run the migrations, then start the parts:

```bash
scripts/stack.sh db          # the migrations, a one-off job: do this first, and again after every release
scripts/stack.sh web
scripts/stack.sh gateway
scripts/stack.sh agent
```

or all four on one machine: `scripts/stack.sh all`. On Windows use `scripts\stack.ps1` with the same names.

4. **Check what you filled in:** `python -m salesai doctor` (see `docs/GO_LIVE_CHECKLIST.md`). It tells you which setting to change; run it again after each change.

## Running only some of them

Any subset works. Examples:

- **Try the site without WhatsApp:** `db`, `web`. Sign-in codes and connecting a number wait for the gateway, and tell the owner
  to try again if it is not running.
- **A machine just for Meta traffic:** `db` once from anywhere, then `gateway` on its own box with a public address for `receive`.
- **More AI capacity:** `scripts/stack.sh agent --scale agent=3` on as many machines as you like. Replicas are safe: the queue
  gives each conversation to one worker at a time.
- **More send capacity:** `scripts/stack.sh gateway --scale send=2` (Meta's per-number limits still apply).

Stopping one part does not break the others. Messages from customers keep being stored while the agent is down, and are answered
when it is back (nothing acknowledged is lost, INV-6). Look at a part with `scripts/stack.sh <part> ps` or `logs`.

## What each part needs from you

| To set up | Part | Where it comes from |
|---|---|---|
| Database URLs (four) | all | your Postgres host; roles from `db/bootstrap_roles.sql` |
| `MASTER_KEY` | admin, gateway | `openssl rand -base64 32`. **Back it up: lose it and every business must reconnect WhatsApp** |
| `JWT_SECRET`, `OTP_SECRET` | web | 32+ random characters each |
| `META_APP_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `META_CONFIG_ID`, `META_GRAPH_VERSION` | gateway (id, config and version also web) | `docs/WHATSAPP_GO_LIVE.md` |
| `PLATFORM_SENDER_PHONE_NUMBER_ID`, `PLATFORM_WA_ACCESS_TOKEN` | gateway | our own WhatsApp number, for sign-in codes |
| The model: `LLM_*`, `OPENAI_COMPAT_*` | agent | `deploy/agent/.env.example`; default is `openai/gpt-oss-20b`. Switching model or host is an edit and a restart |

## Same image, different modes

Every part builds the **same** backend image (`chatdeal-backend:local`) and starts it in a different mode, so all the parts are
always the same version. The first part you start builds it; the others reuse it (`--build` rebuilds). The site is the only
part with its own image, built from `frontend/`.

## Keeping it honest

`backend/tests/test_deploy_files.py` reads these compose files and checks, without needing Docker, that every service starts
under the application's production rules, holds exactly the secrets its part needs and no more, passes only settings the
application knows, and has every variable it uses documented in its `.env.example`. Add a secret to a service and the test
fails until the change is deliberate.

The older all-in-one demo (`scripts/up.sh --demo`, `deploy/docker-compose.yml`) is separate and unchanged: one machine, a
Postgres container, demo flags on. It is not for production.
