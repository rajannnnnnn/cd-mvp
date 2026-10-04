# WhatsApp AI Sales Assistant (working name: "Saathi")

A multi-tenant SaaS for India. A small business connects its **own WhatsApp number**; an AI assistant answers customers
in the owner's voice, quotes prices **only** within limits the owner sets, negotiates in small steps, captures orders and
visits, and brings the owner in when it matters. The owner stays in control from WhatsApp or a web app.

"Saathi" is a placeholder brand (one constant, `VITE_BRAND`; see `docs/RUNNING.md`). The product spec is decided; the
engineering decisions are recorded as ADRs.

## Status at a glance (see `docs/PROGRESS.md` for the full picture)

| Area | State |
|---|---|
| Backend (API, async pipeline, pricing engine, agent, delivery, owner loop, auth, operator functions) | **Built and tested** (about 190 automated tests, real Postgres, no mocks of product logic) |
| Frontend (marketing site, owner app, operator console, playground) | **Built**; app in English, marketing site in English and Hindi; browser tests run against the dev stack |
| Meta WhatsApp Cloud API | Adapter and Embedded Signup completion **implemented against documented formats and a fake Graph server; not yet verified with a real Meta number** (none exists yet) |
| LLM | Deterministic local stand-in is the default (no API key yet). Anthropic adapter implemented and tested against a local fake of the Messages API; **real-model evaluations not yet run** |
| Self-serve SaaS layer (sign-up by mobile number, onboarding wizard, shop addresses, pricing and billing with GST invoices, analytics, reports) | **Built and tested in the simulated environment**; any one-time code is accepted in demo mode; no real money moves (test gateway) |
| Voice notes | **Deferred by the founder** (seam kept, ADR 0015) |
| Deployment | **Not deployed (by decision).** Compose stack and images are defined; they have not been built with a Docker daemon yet (CI does it) |

## Run it

```bash
scripts/up.sh --demo          # Docker: whole product on one machine, with demo shops and conversations
# then open http://localhost:8080  (marketing site), /app (the app)
```
Local development without Docker, tests, and every other command: **`docs/RUNNING.md`**.

## Read these, in this order

1. `docs/PROGRESS.md` – where the project stands, what is verified, what is open, **what to do next**.
2. `CLAUDE.md` – the engineering operating rules (invariants, milestones, definition of done).
3. `docs/PRD.md` – what to build and why (requirements `FR-*`, `NFR-*`, `CR-*`, scope by version).
4. `docs/TECHNICAL_DESIGN.md` – invariants `INV-1…12`, required properties, "built for change" seams.
5. `docs/HLD.md` – the required system shape.
6. `docs/decisions/` – ADRs: every significant technical decision with context and how to revisit it.
7. `docs/CODEMAP.md` – where things live, and which tests prove which invariant.

## Repository layout

```
backend/    Python 3.11 / FastAPI modular monolith (one image, several process roles)
  src/salesai/modules/   domain modules: tenants, catalog, pricing, sales, handoffs, channels, auth,
                         delivery, agent, conversations, notifications
  migrations/            versioned SQL migrations (source of truth for the schema)
  tests/                 pytest suite (real Postgres; Redis for the second queue backend)
  scripts/               boundary checker (CI gate), OpenAPI export, golden-file generator
frontend/   React + TypeScript + Vite: static marketing site (/) and client-side-rendered app (/app)
  e2e/                   Playwright browser tests (and screenshot helpers)
db/         role bootstrap SQL and a generated schema snapshot
deploy/     docker-compose stack
scripts/    one-command start, dev environment, schema dump
docs/       product, design, decisions, progress
.github/    CI
```

## For a coding agent starting fresh

Read `docs/PROGRESS.md` ("Next steps" lists the work in priority order and says exactly what is blocked on the founder),
then follow `CLAUDE.md`'s session routine. Do not weaken an invariant to make a test pass; ask the founder when a choice
changes product behaviour, needs a paid commitment, or touches an invariant.
