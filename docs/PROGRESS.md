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
- **Full self-serve SaaS** (added 2026-10-04): marketing home, pricing and ad pages; sign-up and sign-in by mobile number with **any one-time code
  accepted for now** (the real OTP comes once the company exists); onboarding (business, products, WhatsApp, assistant preferences); shop
  addresses (`/<shop-name>` lands on the dashboard); analytics, reports, AI preferences; billing. **The app is English only**; voice notes are
  removed from the product surface. Operations must work even where real messaging or payments cannot be tested yet.

## 3. Milestone status (from `CLAUDE.md`)

Legend: **Done** = implemented and covered by automated tests. **Partial** = implemented, with the gaps listed.

| # | Milestone | Status | Evidence / gaps |
|---|---|---|---|
| M1 | Foundations | **Done, one gap** | ADRs 0001–0015, repo layout, validated config, health/readiness on every role, Dockerfiles, compose, CI with boundary gate, one-command start. Gap: images never built with a Docker daemon in the authoring environment (CI builds them). |
| M2 | Data and tenancy | **Done** | RLS + composite FKs + four DB roles; floors in their own table behind SECURITY DEFINER functions; cross-tenant tests on every table/view/role (`test_tenant_isolation.py`); export and hard delete; migrations separate; `db/schema.sql` snapshot. |
| M3 | Async backbone | **Done** | Queue interface with Postgres and Redis backends passing one property suite; whole pipeline also passes on Redis; versioned event envelope with transactional outbox and relay. |
| M4 | Channels | **Partial** | Simulator channel + dev chat view + signed-webhook ingress meeting NFR-1/INV-6; WhatsApp Cloud adapter; all Meta event types handled **from synthetic payloads written from documentation**, not recordings (no Meta number yet). NFR-1 load test written and run (section 4). |
| M5 | Conversation engine | **Done** | End-of-turn heuristic, batching, interrupts, owner takeover, stop/start, personal/opt-out, 24h window; behaviours 1–7 tested end to end. |
| M6 | Pricing engine | **Done** | Pure engine, hypothesis property tests (mutation-checked), golden file shared with the marketing demo. |
| M7 | Agent | **Partial** | Context assembly, planner → engine → writer → checks pipeline, versioned prompts, turn records, resilient provider chain, Anthropic adapter tested against a fake Messages API. Conversation evaluation suite built (`backend/tests/evals/`, 32 scenarios, invariant graders) and **passes 32/32 with the local stand-in**. **Missing: running it against real models and measuring cost per conversation** (needs an API key). |
| M8 | Delivery | **Done** | Paced multi-part replies, per-business timing parameters, send-time checks (version, window, pause, opt-out, number status), per-number rate limit. |
| M9 | Owner loop | **Done** | Handoff/deal alerts, knowledge gaps, stop/start, daily summary, config-by-chat with read-back confirmation, disconnection handling, quality alerts. |
| M10 | Media (voice) | **Deferred by the founder** | ADR 0015. Audio is stored and answered with a polite "please type"; no transcriber. |
| M11 | API | **Done** | Every PRD capability under `/api/v1`, OpenAPI contract committed, generated typed client, roles (owner, staff, operator), idempotency keys, consistent errors, SSE live updates. Embedded Signup completion endpoint implemented against a fake Graph server (**unverified with real Meta**). |
| M12 | Frontend | **Done** | Marketing site (EN/HI, live price-limits demo, legal pages), login, dashboard, chats, pipeline, inbox, catalog (write-only floor, ladder preview), offers, "My voice" style examples (how the owner writes; not voice notes), customers, settings (including the owner's own data export and delete), playground, operator console. **The app is English only by founder decision**; the marketing site keeps its EN/HI toggle. Voice notes appear nowhere in the product surface. The in-app "Connect a WhatsApp number" button exists but is unverified against Meta. |
| M13 | Hardening | **Partial** | Structured JSON logs with redaction and a request id on every API call, Prometheus metrics, operator alerts **routed to a webhook and/or WhatsApp** (ADR 0017), scheduler retention, evaluation suite, load-test harness with results (section 4). **Missing: distributed tracing across the queue hops, a run of the evaluations on real models.** |
| M14 | Meta onboarding | **Partial** | Embedded Signup completion, account/quality events, disconnection handling are implemented and tested against fakes. **Needs verification with a real Meta test number**; the browser-side Embedded Signup button is built but tested only against fakes. |
| M15 | SaaS layer (founder request, 2026-10-04) | **Done in the simulated environment** | Self-serve sign-up by mobile number (any code accepted in demo mode), onboarding wizard (business, details, products, WhatsApp, assistant preferences, go live), shop addresses `/app/<slug>` with `/<slug>` short links, marketing home + pricing + ad landing + contact pages, billing (plans, 14-day trial, subscriptions, GST invoices, usage and limits, test and manual payment providers, operator reconciliation), analytics, reports with CSV export. **Unverified:** the Caddy short-link rule (no Caddy binary here), real payments, real OTP delivery. ADRs 0018, 0019. |

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
- Async pipeline throughput: one `all` process drained thousands of queued turns at roughly 2 turns/s on a shared machine, far above NFR-5's 5,000 conversations/day. End-to-end conversation test (`loadtest.run conversations`: 60 new customers × 4 messages over the 3 demo shops, one worker process, human-like
pacing ON): 240 messages, **0 dead letters, 0 failed sends**; agent pipeline p50 1 ms / p95 2 ms (stand-in model). The first run reported message-to-first-reply
p95 105 s. **Diagnosis:** that run overlapped a 1,500-message flood from one customer on one shop's number, and outbound actions were serialized
per *number* (ordering key `num:<id>`, contradicting the per-conversation rule in the Technical Design), so everyone on that number queued behind the flood
at the 5 msgs/s per-number limit (Meta's coexistence throughput). The other two shops answered normally. **Fixed:** outbound actions are now ordered per
conversation (`conv:<id>`); the per-number token bucket stays shared. A per-conversation flood guard (`MAX_AI_TURNS_PER_CONVERSATION_10MIN`, default 30) records
but no longer answers messages beyond the budget. Both have tests. **Re-measured on a clean database, nothing else running** (same 60 customers × 4 messages): all 240 messages handled in 20 s
(was 252 s), 0 dead letters, 0 failed sends, agent pipeline p95 1 ms, **message-to-first-reply p50 8.5 s / p95 12.2 s** (was p95 105 s). That
figure still contains the intentional human-like delay (reading, typing; up to 20 s by default), so NFR-2 (excluding pacing) holds for the
stand-in model; it must be re-measured with a real model.
- Browser suite: **36/36** passing against the dev stack on a freshly seeded database (desktop and mobile projects), including the whole path
  marketing → sign-up → onboarding → live → billing → analytics, shop-address links, and paying in test mode. Backend: **273 tests passing** (including
  billing, onboarding, analytics, alert routing, migrations-with-data) and evaluations 32/32 (run with `env -u OTP_PER_PHONE_PER_HOUR -u OTP_PER_IP_PER_HOUR`
  if your shell has the dev `.env` loaded, because one test checks the default rate limits). Not re-run this session: the Redis-backend pipeline run.

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
7. **The app is English only** (founder decision, 2026-10-04). Every user-facing string goes through `t(key, en)` or `tr(en)` (`src/i18n`), so a
   language can be added later without touching screens. The marketing site keeps its EN/HI toggle (`src/site/hi.ts`, guarded by `hi.test.ts`).
8. The marketing "Start a pilot" button is a `mailto:` link (`VITE_CONTACT_EMAIL`, placeholder address).
9. Redis backend runs standalone/Sentinel (single hash tag); sharding is a later step.
10. Template management: the product sends only inside the 24-hour window; a window-closed send fails safely into an owner handoff
    (INV-8, INV-12). No owner-defined WhatsApp templates exist yet, and none are needed until the founder wants proactive outreach.
11. **`OTP_ACCEPT_ANY`** lets anyone sign in as any number. It is refused in `ENV=production`, but any other environment that is reachable
    from the internet with real data is exposed. Turn it off (and set up WhatsApp delivery) before real customers.
12. **Plan prices, limits and the invoice seller details are placeholders** (`plans.py`, `INVOICE_SELLER_*`). No real money moves: the test
    gateway settles instantly and `manual` needs the platform team to confirm. There is no card/UPI gateway adapter yet (ADR 0019).
13. **Caddy short links** (`/<shop>` → `/app/<shop>`) and clean URLs are written for production but could not be exercised here (no Caddy
    binary); the Vite dev server implements the same rules and is browser-tested. The new marketing pages (pricing, start, contact) are English only.
14. Billing emails/WhatsApp reminders (trial ending, invoice due) are not sent; the app shows banners and the operator is alerted.

## 6. Decisions needed from the founder

| Decision | Why it matters | Default if none |
|---|---|---|
| Confirm ADR 0006 (floor can surface as the final quoted price) or choose "stop above the floor" | Product behaviour and INV-1 interpretation | Keep as built |
| Product name, domain, legal entity, contact address | Branding, legal pages, Meta account ownership | "Saathi", placeholder address |
| LLM vendor/model approval and an API key (paid) | Real evaluations, cost per conversation, pricing | Local stand-in only |
| Meta developer account / test number / Tech Provider path | Real-world verification of the adapter | Simulated network only |
| Pilot shops and categories (CR-5 excludes prohibited categories) | Seed data, evaluation scenarios | Demo shops |
| Real plan prices, included conversations and overage rates | Placeholders are live on the pricing page and in invoices | ₹999 / ₹2,499 / ₹5,999 per month + 18% GST |
| Company name, address and GSTIN for invoices and legal pages; a payment gateway account (Razorpay is the natural fit) | Invoices show bracketed placeholders; payments can only be collected by transfer | Placeholders; manual payments |
| Default AI negotiation per item (recommended: off, owner opts in) | Product default | Off unless the owner enables |

## 7. Next steps, in priority order

**P0 – make the demo stack boringly reliable**
1. Run `scripts/up.sh --demo` where a Docker daemon exists; fix whatever breaks (images, Caddy routes, CSP, `config.json`,
   role passwords); confirm the CI `compose` and `e2e` jobs go green. Record results here.
2. Run the whole browser suite (`frontend/e2e`) against the compose stack and fix any environment-specific failures.
3. Frontend polish pass: empty and error states, skeletons, keyboard navigation, low-end Android check (bundle sizes are
   small; measure on throttled CPU).

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
8. Observability: distributed trace ids across the queue hops (an API request id exists today) and dashboards for the existing
   metrics; tighten mypy overrides (ADR 0010). Configure `ALERT_WEBHOOK_URL` / `ALERT_WHATSAPP_NUMBERS` and get the `operator_alert`
   template approved in Meta before a pilot.
9. Meta Data Use Checkup reminder and a quality-rating based throttling policy.

**Out of scope until the founder says so:** voice-note transcription, v2/v3 items in `docs/PRD.md`, card/UPI gateway integration and refunds.

## 8. Working in this repository

- Gates and commands: `docs/RUNNING.md`. Where code and proof live: `docs/CODEMAP.md`. Decisions: `docs/decisions/`.
- Commit style: small commits referencing requirement and invariant IDs; end with the attribution lines the environment
  asks for. Push to the branch named by the session (never to another branch without permission).
- Environment gotchas: see "Container/CI quirks" in `docs/RUNNING.md`.

## 9. Session log

- **Session 1 (planning):** documents split into `docs/` (PRD, HLD, Technical Design), operating rules in `CLAUDE.md`.
- **Session 2 (build):** backend M1–M9 and M11 built test-first (real Postgres, simulated network); authentication,
  operator functions, scheduler, seed data; frontend foundation, dashboard, chats.
- **Session 3:** remaining owner screens, operator console, playground; marketing site and legal pages;
  self-hosted fonts; containers and compose; boundary checker, lint and strict typing gates, CI; Redis queue backend and
  shared suite; Anthropic adapter and Embedded Signup completion tested against local fakes; ADRs; browser and unit tests;
  this handoff documentation.
- **Session 4 (this one):** reply-latency root cause fixed (outbound ordering per conversation, flood guard; ADR 0016); owner self-service data
  export and delete; alert routing (ADR 0017); voice notes removed from the product surface; app made English-only; then the full self-serve
  SaaS slice requested by the founder (milestone M15; ADRs 0018 and 0019): sign-up, onboarding, shop addresses, marketing pricing/ad/contact
  pages, billing, analytics, reports, operator billing console. A migration bug that only shows on databases with data (row-level security hid
  the rows being backfilled) was found on the dev database and is now guarded by `test_migrations.py`.
