# Running, testing and operating the project

## 1. Everything in Docker (one machine, one command)

```bash
scripts/up.sh --demo      # builds images, generates deploy/.env with fresh secrets, starts the stack, loads demo data
scripts/up.sh --down      # stop        scripts/up.sh --reset    # stop and delete the database volume
```
Open `http://localhost:8080` (marketing site) and `/app` (the product). The stack is `postgres`, one-off `migrate`,
`api`, `ingress` (webhook receiver), `worker`, `scheduler`, `web` (Caddy), plus optional `redis` and `seed` profiles
(`deploy/docker-compose.yml`). Demo mode (`APP_ENV=staging`, simulated WhatsApp, local AI stand-in) is selected by
environment variables only; production refuses to start in that mode.

**Demo sign-in.** The WhatsApp network is simulated: on the login page choose a demo number and the login code appears in
the simulated phone and is filled in automatically.
Owner `+91 99999 00001` (shop "Sharma Sarees & Fabrics"), operator `+91 99999 00000`; other demo shops: Gupta Mobile Point
(`+91 99999 00011`), Fresh Basket Kirana (`+91 99999 00021`).

Status of this path: the compose file validates (`docker compose config`) and the backend wheel, role start-up and
migrations were exercised outside Docker, but **the images have not been built with a Docker daemon in the authoring
environment**. CI (`compose` and `e2e` jobs) builds them and runs the browser suite against the running stack. If the first
`scripts/up.sh` run fails, fix it and record the fix in `docs/PROGRESS.md`.

## 2. Local development without Docker

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 22, PostgreSQL 16 client and server, optionally `redis-server`.

```bash
# database and environment
scripts/dev_env.sh                                  # creates DB salesai_dev, the four roles, and .env (needs a local Postgres superuser)
# backend
cd backend && uv sync --group dev && . .venv/bin/activate
set -a; . ../.env; set +a
python -m salesai migrate                           # schema migrations (separate step, never at process start)
python -m salesai.seed --conversations --spread-days 6    # demo shops + conversations played through the real pipeline
python -m salesai all                               # api + ingress + worker + scheduler on :8000
# frontend (another shell)
cd frontend && npm ci && npx vite --port 5173 --host 127.0.0.1     # http://127.0.0.1:5173 ; /public/config.json points at :8000
```
Useful dev settings in `.env`: `OTP_MIN_INTERVAL_S=0` and high `OTP_PER_PHONE_PER_HOUR` for repeated sign-ins.
Every setting is documented in `.env.example` (a test fails if one is missing).

Container/CI quirks seen in the authoring environment: the Postgres server needed `127.0.0.1` set to `trust` in
`pg_hba.conf`; the Vite dev server must be restarted after editing `tailwind.config.js` or `vite.config.ts`; do not kill
processes with `pkill -f vite` from a shell whose own command line contains the word (it kills the shell).

## 3. Quality gates (what CI runs)

```bash
cd backend && . .venv/bin/activate
ruff check src tests scripts
mypy src
python scripts/check_boundaries.py                  # module boundaries ("built for change" seams)
pytest -q                                           # full suite on the Postgres queue (needs a Postgres superuser; TEST_PG_SUPERUSER_URL)
TEST_QUEUE_BACKEND=redis pytest -q tests/test_queue_redis.py tests/test_inbound.py tests/test_conversation_flow.py \
    tests/test_owner_loop.py tests/test_scheduler.py tests/test_api.py tests/test_auth.py     # whole pipeline on Redis
python scripts/export_openapi.py                    # regenerate backend/openapi.json after any API change
cd ../frontend
npm run gen:api                                     # regenerate src/api/schema.d.ts from openapi.json
npm run typecheck && npm test && npm run build
E2E_CHROME=/path/to/chromium npx playwright test    # browser tests; needs the dev stack (or E2E_BASE_URL) and demo data
```
Tests use a real Postgres (a fresh database per session), the real queue, workers and pipeline, and the simulated network.
Vendors are replaced only by local servers that speak their wire format (`tests/fake_anthropic.py`, `tests/fake_graph.py`);
Redis tests start a throw-away `redis-server` (or use `TEST_REDIS_URL`).

Conversation evaluations (`backend/tests/evals/`, 32 scenarios in Hindi/English/Hinglish over three business types, with
invariant graders on every scenario) run with the normal suite against the local stand-in. To evaluate a real model:
`LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... EVAL_REPORT=eval-<model>.json pytest tests/evals -q` (costs money; the report
holds transcripts, failures, tokens, cost per conversation and latency).

Load tests against a running stack (`backend/loadtest/run.py`; needs the seeded demo data):
`python -m loadtest.run webhook --base http://127.0.0.1:8001 --requests 1500 --concurrency 5` (NFR-1/NFR-3) and
`python -m loadtest.run conversations --base ... --customers 60` (NFR-2/NFR-5); results land in `backend/loadtest/results/`.
Start a dedicated webhook receiver for these with `HTTP_PORT=8001 python -m salesai ingress`.

Regenerating derived files: `scripts/dump_schema.sh` (db/schema.sql snapshot), `backend/scripts/gen_golden.py`
(price-schedule golden file, then re-run both test suites).

## 4. Configuration

All environment-specific values are validated at startup (`backend/src/salesai/config.py`); an invalid value stops the process.
Per-business behaviour (timing, limits, sales style) is data in the database. Frontend runtime config is `/config.json`
(`{"apiBase": ""}` = same origin). Brand and contact address for the marketing site are build args:
`VITE_BRAND`, `VITE_CONTACT_EMAIL` (compose: `BRAND`, `CONTACT_EMAIL`).

## 5. Going to production (checklist; nothing here has been done)

1. Legal entity and Meta Business Account; Meta app with WhatsApp product; approve the Embedded Signup configuration;
   authentication template for login codes; webhook URL `https://<domain>/webhooks/whatsapp` with `META_VERIFY_TOKEN`.
2. Set `ENV=production`, `SIMULATOR_ENABLED=false`, `OTP_CHANNEL=whatsapp_cloud`, `LLM_PROVIDER=anthropic` + key, real secrets
   (`openssl rand -base64 32`), `META_APP_ID`, `META_CONFIG_ID`, `META_APP_SECRET`. The process refuses to start otherwise.
3. Managed Postgres in an Indian region (roles from `db/bootstrap_roles.sql`), run `python -m salesai migrate` as a separate step.
4. Run behind TLS (the Caddy image does it when `SITE_ADDRESS` is a domain), or put the static files on a CDN and route
   `/api` and `/webhooks` to the backend (see `frontend/deploy/Caddyfile` for the rules).
5. **Verify against a real Meta test number** before the first pilot: replace the synthetic webhook fixtures in
   `backend/tests/` with recorded payloads, exercise Embedded Signup completion, template sending, status/echo webhooks.
6. Run the conversation evaluation suite on the chosen models, then load tests for NFR-1/2/5 (both still to be written).
7. Fill the bracketed placeholders in the legal pages (`frontend/privacy.html`, `terms.html`, `data-deletion.html`) and have a lawyer review them.
