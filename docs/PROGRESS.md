# PROGRESS

**Read this first in every session** (see `CLAUDE.md`). It is the project's memory: where things stand, what is verified,
what is open, and what to do next. Keep it current; update it at the end of every session.

_Last updated: 2026-10-04._

## 1. The product in two paragraphs

A multi-tenant SaaS for India. A small business connects its own WhatsApp number; an AI sales assistant answers customers
in the owner's voice and language (Hindi, English, Hinglish), quotes prices only from a deterministic pricing engine within
limits the owner sets (including a private lowest price the AI never sees), negotiates in small steps, captures orders and
store visits, and brings the owner in on complaints, unknown questions and requests for a human. The owner steers from
WhatsApp or a web app; an operator console serves the platform team.

The immediate goal is a **working, production-grade MVP to show the co-founders**: the real system end to end, with the
WhatsApp network simulated (no Meta account exists yet) and a deterministic stand-in for the language model (no API key yet).
Everything else about the product is real code, not mock-ups.

## 2. Standing directives from the founder (still in force)

- **Build and test; do not deploy.** The founder will handle deployment later. Provide containers and compose, nothing more.
- **Never mock product behaviour.** Only external vendors may be replaced, by local servers that speak their wire format.
  The language-model stand-in must be real, runnable code selected purely by configuration.
- **Implement the full data model and features in scope now**, not placeholders "for later". Stay within PRD v1 scope.
- **Backend and frontend are separate deployables**; Docker Compose runs them together on one machine.
- **Small-startup focus:** simple operations, few moving parts, cheap to run.
- **Identity is the WhatsApp number.** Authentication is one-time codes delivered over WhatsApp.
- **Client-side-rendered app; static hosting is fine.** A marketing site comes first, then login.
- **Voice notes are out of scope for now** (ADR 0015; the seam is kept).
- **The frontend must be impressive and honest** for the co-founders, in English and Hindi, mobile first.
- "Saathi" is a **placeholder brand** (`VITE_BRAND`); the founder has not chosen a name or legal entity.
- No pull requests unless the founder asks.

## 3. Milestone status (from `CLAUDE.md`)

Legend: **Done** = implemented and covered by automated tests. **Partial** = implemented, with the gaps listed.

| # | Milestone | Status | Evidence / gaps |
|---|---|---|---|
| M1 | Foundations | **Done, one gap** | ADRs 0001–0015, repo layout, validated config, health/readiness on every role, Dockerfiles, compose, CI with boundary gate, one-command start. Gap: images never built with a Docker daemon in the authoring environment (CI builds them). |
| M2 | Data and tenancy | **Done** | RLS + composite FKs + four DB roles; floors in their own table behind SECURITY DEFINER functions; cross-tenant tests on every table/view/role (`test_tenant_isolation.py`); export and hard delete; migrations separate; `db/schema.sql` snapshot. |
| M3 | Async backbone | **Done** | Queue interface with Postgres and Redis backends passing one property suite; whole pipeline also passes on Redis; versioned event envelope with transactional outbox and relay. |
| M4 | Channels | **Partial** | Simulator channel + dev chat view + signed-webhook ingress meeting NFR-1/INV-6; WhatsApp Cloud adapter; all Meta event types handled **from synthetic payloads written from documentation**, not recordings (no Meta number yet). Load test for NFR-1 not written. |
| M5 | Conversation engine | **Done** | End-of-turn heuristic, batching, interrupts, owner takeover, stop/start, personal/opt-out, 24h window; behaviours 1–7 tested end to end. |
| M6 | Pricing engine | **Done** | Pure engine, hypothesis property tests (mutation-checked), golden file shared with the marketing demo. |
| M7 | Agent | **Partial** | Context assembly, planner → engine → writer → checks pipeline, versioned prompts, turn records, resilient provider chain, Anthropic adapter tested against a fake Messages API. Conversation evaluation suite built (`backend/tests/evals/`, 32 scenarios, invariant graders) and **passes 32/32 with the local stand-in**. **Missing: running it against real models and measuring cost per conversation** (needs an API key). |
| M8 | Delivery | **Done** | Paced multi-part replies, per-business timing parameters, send-time checks (version, window, pause, opt-out, number status), per-number rate limit. |
| M9 | Owner loop | **Done** | Handoff/deal alerts, knowledge gaps, stop/start, daily summary, config-by-chat with read-back confirmation, disconnection handling, quality alerts. |
| M10 | Media (voice) | **Deferred by the founder** | ADR 0015. Audio is stored and answered with a polite "please type"; no transcriber. |
| M11 | API | **Done** | Every PRD capability under `/api/v1`, OpenAPI contract committed, generated typed client, roles (owner, staff, operator), idempotency keys, consistent errors, SSE live updates. Embedded Signup completion endpoint implemented against a fake Graph server (**unverified with real Meta**). |
| M12 | Frontend | **Done, polish pending** | Marketing site (EN/HI, live price-limits demo, legal pages), login, dashboard, chats, pipeline, inbox, catalog (write-only floor, ladder preview), offers, voice examples, customers, settings, playground, operator console. Hindi covers the marketing site fully and the app shell/main labels only partially. The in-app "Connect a WhatsApp number" button exists but is unverified against Meta. |
| M13 | Hardening | **Partial** | Structured JSON logs with redaction, Prometheus metrics, operator alerts, scheduler retention, evaluation suite, load-test harness with first results (section 4). **Missing: alert routing, tracing, a run of the evaluations on real models.** |
| M14 | Meta onboarding | **Partial** | Embedded Signup completion, account/quality events, disconnection handling are implemented and tested against fakes. **Needs verification with a real Meta test number**; the browser-side Embedded Signup button is not built. |

## 4. What has been verified, and how

Automated (run in CI, commands in `docs/RUNNING.md`): backend lint, strict typing (with the documented relaxation in ADR
0010), module-boundary check, the full backend suite on the Postgres queue, the async pipeline on the Redis queue, API
contract drift, frontend typecheck, unit tests and build, compose validation. Browser tests (`frontend/e2e/`) cover the
marketing site, authentication, every owner screen, the floor-privacy rule in the UI, a customer conversation in the
playground (including "never below the floor"), the operator console, and mobile layouts.

Verified by hand in the authoring environment: the installed backend wheel starts every process role and serves the
role-specific routes; migrations apply from scratch; the dev stack is reproducible from `docs/RUNNING.md` (database,
migrations, seed, backend, frontend); screenshots reviewed for every screen at desktop width, and the marketing site and
login on mobile and in dark mode.

**Measured (authoring machine, dev stack, language-model stand-in, so zero model latency):**
- NFR-1 webhook acknowledgement: 1,500 signed webhooks at concurrency 5 → p50 11 ms, p95 18 ms, **p99 25 ms** (limit 300 ms), 414 requests/s on one ingress process; at concurrency 30 the single process saturates near 320 req/s and p99 rises to ~345 ms, so scale ingress horizontally beyond that. NFR-3: **every acknowledged message was stored** (1,500 of 1,500), zero inbound dead letters.
- Async pipeline throughput: one `all` process drained thousands of queued turns at roughly 2 turns/s on a shared machine, far above NFR-5's 5,000 conversations/day. The end-to-end conversation load test (`loadtest.run conversations`) has not produced recorded results yet; run it and record them here.
- Browser suite: 29/29 passing against the dev stack (desktop and mobile projects). Backend: 209+ tests passing; evaluations 32/32.

**Not verified:** anything against real Meta infrastructure; any real language model; Docker image builds and the compose
stack (CI will); latency with a real model (NFR-2 adds the model's time); behaviour on real low-end Android devices.

## 5. Known issues and limitations

1. **Synthetic Meta fixtures.** All webhook payloads are written from Meta's documentation. Replace/supplement them with
   recorded payloads from a real test number (contract tests) before any pilot.
2. **No real-model evaluation.** The deterministic stand-in's language quality is intentionally modest. The conversation
   evaluation suite (Hindi, English, Hinglish; several business types; invariants asserted) must be written and run once an
   API key exists; model choice and cost per conversation depend on it. `PRICES_USD` in `llm/base.py` are estimates to be
   verified against current published pricing.
3. **INV-1 interpretation needs founder confirmation** (ADR 0006): the last concession step equals the floor and is issued
   to the customer as a final price. The floor value is never exposed otherwise.
4. **Hypothesis flake:** one property test failed once and could not be reproduced (6,000 targeted and 3,000 full-run
   examples passed afterwards). Treat any recurrence as real and capture the seed.
5. **Static typing relaxation** in data-access modules (ADR 0010) is a ratchet to tighten over time.
6. **Legal pages are drafts** with bracketed placeholders (entity, address, hosting, providers, grievance officer).
7. **Frontend Hindi** is complete for the marketing site. In the app only the shell, dashboard, chats and login are
   translated (46 keys); the screens added later (pipeline, inbox, catalog, offers, voice, customers, settings, playground,
   operator) still use English strings. Wrap them in `t('key', 'English')` and add entries to `src/i18n/hi.ts`; a vitest guard
   fails if a wrapped string lacks Hindi.
8. The marketing "Start a pilot" button is a `mailto:` link (`VITE_CONTACT_EMAIL`, placeholder address).
9. Redis backend runs standalone/Sentinel (single hash tag); sharding is a later step.
10. No in-app flow for the owner to export or delete their own data; today this is an operator action
    (`/operator/businesses/{id}/export`, `DELETE`), described on the data-deletion page.

## 6. Decisions needed from the founder

| Decision | Why it matters | Default if none |
|---|---|---|
| Confirm ADR 0006 (floor can surface as the final quoted price) or choose "stop above the floor" | Product behaviour and INV-1 interpretation | Keep as built |
| Product name, domain, legal entity, contact address | Branding, legal pages, Meta account ownership | "Saathi", placeholder address |
| LLM vendor/model approval and an API key (paid) | Real evaluations, cost per conversation, pricing | Local stand-in only |
| Meta developer account / test number / Tech Provider path | Real-world verification of the adapter | Simulated network only |
| Pilot shops and categories (CR-5 excludes prohibited categories) | Seed data, evaluation scenarios | Demo shops |
| Subscription price (after cost measurement) | Marketing copy says "pricing after pilot" | Unpriced pilot |
| Default AI negotiation per item (recommended: off, owner opts in) | Product default | Off unless the owner enables |

## 7. Next steps, in priority order

**P0 – make the demo stack boringly reliable**
1. Run `scripts/up.sh --demo` where a Docker daemon exists; fix whatever breaks (images, Caddy routes, CSP, `config.json`,
   role passwords); confirm the CI `compose` and `e2e` jobs go green. Record results here.
2. Run the whole browser suite (`frontend/e2e`) against the compose stack and fix any environment-specific failures.
3. Frontend polish pass: finish Hindi for app screens (every `t('key', 'English')` call needs a `hi.ts` entry), empty and
   error states, skeletons, keyboard navigation, low-end Android check (bundle sizes are small; measure on throttled CPU).

**P1 – close the verification gaps**
4. Run the conversation evaluation suite (`backend/tests/evals/`, command in `docs/RUNNING.md`) against each candidate model
   once an API key exists; compare safety first, then cost and latency; record the choice and cost per conversation in a new
   ADR. Grow the scenario list with every real failure found in pilots.
5. Extend the load tests with a real model (NFR-2 p95 < 8 s) and a larger tenant count (NFR-5: 50 businesses, 5,000
   conversations/day); the harness is `backend/loadtest/run.py`.
6. Replace synthetic Meta payloads with recordings from a real test number; add contract tests per event type.
7. Verify the whole Embedded Signup flow with a real Meta number: the browser side (Settings → Numbers → "Connect a
   WhatsApp number", `frontend/src/features/settings/signup.ts`) and the server side (`POST /api/v1/numbers/connect`) are
   implemented from Meta's documentation and tested only against fakes.

**P2 – product depth already in PRD v1**
8. Owner self-service export/delete; operator alert routing (email/WhatsApp to the operator); observability: traces and
   dashboards for the existing metrics; tighten mypy overrides (ADR 0010).
9. Meta Data Use Checkup reminder, quality-rating based throttling policy, template management for window-closed sends.

**Out of scope until the founder says so:** voice-note transcription, v2/v3 items in `docs/PRD.md`, payments/billing.

## 8. Working in this repository

- Gates and commands: `docs/RUNNING.md`. Where code and proof live: `docs/CODEMAP.md`. Decisions: `docs/decisions/`.
- Commit style: small commits referencing requirement and invariant IDs; end with the attribution lines the environment
  asks for. Push to the branch named by the session (never to another branch without permission).
- Environment gotchas: see "Container/CI quirks" in `docs/RUNNING.md`.

## 9. Session log

- **Session 1 (planning):** documents split into `docs/` (PRD, HLD, Technical Design), operating rules in `CLAUDE.md`.
- **Session 2 (build):** backend M1–M9 and M11 built test-first (real Postgres, simulated network); authentication,
  operator functions, scheduler, seed data; frontend foundation, dashboard, chats.
- **Session 3 (this one):** remaining owner screens, operator console, playground; marketing site and legal pages;
  self-hosted fonts; containers and compose; boundary checker, lint and strict typing gates, CI; Redis queue backend and
  shared suite; Anthropic adapter and Embedded Signup completion tested against local fakes; ADRs; browser and unit tests;
  this handoff documentation.
