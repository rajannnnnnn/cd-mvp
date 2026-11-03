# PROGRESS

**Read this first in every session** (see `CLAUDE.md`). It is the project's memory: where things stand, what is verified,
what is open, and what to do next. Keep it current; update it at the end of every session.

_Last updated: 2026-11-02._

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
- The product is named **ChatDeal** (`VITE_BRAND`, renamed from the working name "Saathi" on the founder's instruction); the legal entity and domain are not chosen yet. Browser storage keys moved from `saathi.*` to `chatdeal.*` (everyone signs in once more). Infra identifiers (compose project `saathi`, image tags, Terraform names, `/opt/saathi`) are left as they are on purpose: renaming the compose project would orphan existing local database volumes.
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
| Product name, domain, legal entity, contact address | Branding, legal pages, Meta account ownership | "ChatDeal"; domain and address still placeholders |
| LLM vendor/model approval and an API key (paid) | Real evaluations, cost per conversation, pricing | Local stand-in only |
| Meta developer account / test number / Tech Provider path | Real-world verification of the adapter | Simulated network only |
| Pilot shops and categories (CR-5 excludes prohibited categories) | Seed data, evaluation scenarios | Demo shops |
| Real plan prices and included conversations (no overage rate: ADR 0032) | Placeholders are live on the pricing page and in invoices | ₹999 / ₹2,499 / ₹5,999 per month + 18% GST |
| Company name, address and GSTIN for invoices and legal pages; a payment gateway account (Razorpay is the natural fit) | Invoices show bracketed placeholders; payments can only be collected by transfer | Placeholders; manual payments |
| Default AI negotiation per item (recommended: off, owner opts in) | Product default | Off unless the owner enables |

## 7. Next steps, in priority order

> **Superseded by `docs/PLAN_V1_LOCK.md` (2026-11-02).** That document is the locked v1 plan: the Meta
> constraints as they stand now, unit economics, the four-phase build sequence, and ADRs 0025–0031. The
> list below is kept because its items survive inside that plan, but read the plan first.

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
  Later in the session, on founder request: the Products page (renamed from Catalog; route `/products`, `/catalog` redirects) got cards and grid views,
  Edit / Delete / on-off on every item, tick-select with bulk on/off/delete each ending in a final confirmation, a docked editor that sits beside
  the list instead of over it, and a category field that is typable *and* a dropdown of the shop's existing categories with a "did you mean"
  hint for near-duplicates (`features/catalog/category.ts`, unit-tested). The assistant on/off switch was removed from the sidebar and Home and
  now lives only in Settings → Assistant; changing it needs a code sent to the signed-in number (`POST /business/ai/code`, then `POST /business/ai`
  with `code`; same one-time-code store as sign-in, used once; `OTP_ACCEPT_ANY` accepts any code in demos). Home shows a notice when it is paused.
  Founder follow-ups the same day: Chats and Pipeline share one menu entry (tabs), as do Analytics and Reports; "Playground" became a
  "Try the assistant" button on Chats/Pipeline (and in Settings → Assistant), at `/try`; "My voice" moved into Settings (optional tab;
  `/voice` redirects). The product editor lost the visibility toggle and the out-of-stock option (the list switch does that), shows only price
  types (fixed, starts from, range, on request; one per product, shared by its variants), and keeps bargaining in its own card. Offers got the Products
  layout (cards/grid, switch + confirm, ticks + bulk on/off/delete, docked editor) and can target a whole category as well as one product
  (migration `0007_offer_category.sql`; the pricing role gets column-level SELECT on `products(id, business_id, category)` only).
  Settings → Support (ADR 0020, migration `0008_support.sql`): an owner or staff member opens a ticket in one step, support (operator console →
  Support) answers in the same thread, the owner can mark it sorted or reopen it by replying. Tests: `test_support.py`, `e2e/support.spec.ts`.
  Products and Offers hide their tick boxes until the owner presses **Select**; **Done** (or finishing a bulk action) hides them again.
  Settings tabs are now Shop, Assistant (on/off, try it, selling style, when you step in, optional My voice), Team, Numbers, Security, Support; old `?tab=voice` and `?tab=pacing` links land on Assistant.
  Performance (docs/PERFORMANCE.md): per-action load test on 3M messages / 50 shops; found and fixed slow dashboard/analytics reads, 12 unindexed foreign keys (migrations 0009, 0010; guarded by a test) and an offer save that re-read every offer. ADR 0021 proposes grouping the tables into domains.
  QA round (Jira DEAL-113 to DEAL-119; validation and plan in the session notes). Push A: DEAL-118 prices are plain numbers at every layer (input
  filtering in `lib/money.ts` and `ui/inputs.tsx`, strict API money type with field-named messages, `CHECK > 0` in migration 0011 added NOT VALID so an
  existing zero price cannot block the upgrade; find and fix such rows, then VALIDATE). DEAL-113 mandatory fields carry a red `*` (`Field required`) in
  the product, offer, sign-up, support, shop-settings and team forms, with inline errors and focus on the first missing field. Tests: `test_money_input.py`
  (including a property test), `lib/money.test.ts`, `e2e/forms.spec.ts`.

- **Reply speed removed from Settings** (founder's instruction). Owners no longer choose Quick/Natural/Relaxed; the assistant keeps the stored `timing_params` (default natural pacing). The API field and the pacing engine (M8) are unchanged, so the control can return later without a rewrite.

- **The assistant on/off switch is no longer the shop's** (founder's instruction). Owners and staff have no toggle in the app: Settings → Assistant lost the switch and its code step, `POST /business/ai` and `POST /business/ai/code` are gone (404), and `confirm_code` was removed with them. The platform team switches it in the operator console (`PATCH /operator/businesses/{id}` with `ai_enabled`; switching on is still refused for an ended subscription). When it is off, the menu says "AI assistant paused" and Home points to Support ("Contact support"); while it is on nothing is shown. Billing still pauses it automatically (trial end, cancellation). **Open question for the founder:** the owner's WhatsApp `stop` / `start` commands (PRD FR-CV-10) still work, because they are PRD behaviour; say if they should go too. Tests: `test_the_assistant_switch_belongs_to_the_platform_team_not_the_shop` (backend) and the owner browser spec; `test_api.py` and `test_billing.py` were re-run after the change, the full backend suite was not.


- **Channel gateway, step 1 (2026-10-31).** Gateway part `forward` renamed `inbound` to match the agreed component design (receive / inbound / send):
  `deploy/gateway/docker-compose.yml` (service, `INBOUND_CONCURRENCY`), `.env.example`, `scripts/stack.ps1` comment, ADR 0020. No code change; the queue
  name `inbound.events` is unchanged. Checked with `docker compose config` only: **no Docker daemon, Postgres or uv on this machine, so the backend suite
  was not run.** Still open from ADR 0020: (1) raw webhook log fidelity (exact bytes, signature header, keep bad-signature requests, `RAW_WEBHOOK_RETENTION_DAYS`),
  (2) per-role secrets (only `send` needs `MASTER_KEY`), (3) agent context loader must not read `access_token_enc`, (4) `/metrics` ports for the monitor.
  Next: do (1) and (2) test-first on a machine where the Postgres suite can run.

- **Session 5 (planning only, 2026-11-02).** No product code changed. The founder asked for v1 to be finalised and locked, with Meta
  treated as the primary constraint, and said to raise only feature-level questions. Output: **`docs/PLAN_V1_LOCK.md`** plus **ADRs
  0025–0031** (component contract; channel cost as a budget; AI priorities, caching and evals; admin plane and founder dashboard;
  platform alerting by email to the founder; auditing, retention and DPDP; per-tenant learning loop).
  **Findings that change the plan, all from research this session:**
  (a) Meta's AI Providers clause has been in force since 15 Jan 2026 — it permits a business to retain an AI provider as its service
  provider, and **forbids using WhatsApp data to train or improve any model, even aggregated, except exclusively for that one business**;
  this rules out the documented v2 cross-tenant learned models and bandit tuning, and is proposed to the founder as INV-13.
  (b) Since **1 Oct 2026 service messages are billed** — 1,000 free per business number per month, then ~₹0.115 in India — and billing has
  been per delivered message since Jul 2025, so **every part of a multi-part reply is now money**; nothing in the code counts Meta messages.
  (c) **Prompt caching is not implemented** (no `cache_control` in the agent) and the cheap model slot still points at `claude-haiku-4-5`
  ($1/$5) instead of `claude-haiku-5-5` ($0.10/$0.50) — together the largest cost item, removable at zero quality cost.
  (d) Illustrative variable cost is **~₹2.6 per conversation**, which implies plan inclusions in the low hundreds, not thousands.
  (e) The eval suite proves invariants but **not selling ability**, so it can pass while the assistant is safely useless.
  (f) Embedded Signup v2 was deprecated 15 Oct 2026; our coexistence parameters are already correct, but the Apr 2026 `account_offboarded`
  and `account_reconnected` webhooks are missing from the fixtures.
  **Caveat:** Meta's own domains are blocked by this environment's network policy, so every Meta figure above comes from secondary 2026
  sources and must be re-read against Meta's pages before pilot. The branch was also fast-forwarded from the Oct 12 commit to `b8445a9`.
  **Still only on the founder's machine:** commit `ef05e4f` (client-rendered marketing pages, standalone web stack, `scripts/stack.sh`,
  ADR 0024). ADR number 0024 is reserved for it; do not reuse.

- **Session 5, build (2026-11-02).** Founder: build until the product is complete and only real keys (plus the
  checks that need them) remain. Done and pushed so far:
  - **LLM choice is configuration (ADR 0034), founder's default `openai/gpt-oss-20b` on every stage.** New
    OpenAI-compatible adapter (Groq, Together, Fireworks, OpenRouter or self-hosted vLLM by `OPENAI_COMPAT_BASE_URL`);
    per-stage vendor/model/effort; fallback with its own model; prices as data (`LLM_PRICES`); startup refuses an
    unpriced model (it used to record turns as free), a vendor without its key, a non-claude model on anthropic, and
    the local stand-in anywhere in production.
  - **Prompt caching (ADR 0027):** stable context rendered first and byte-stable (the planner input used to start with
    a timestamp, so nothing could ever cache); cache reads/writes priced per model and logged per call.
  - **Benchmark harness** `backend/scripts/benchmark_models.py`: `cost` (free) and `run --yes` (spends money; ranks by
    invariant failures, then quality, then latency, then cost). Candidates in `backend/benchmark/candidates.json`.
  - Fixed two production-config tests broken by the new default model and strengthened them to assert each rule's own
    reason (one case had been passing for an unrelated reason). Fixed two pre-existing CI failures (an import order,
    an Optional row in `support.py`).
  - Verified: the 46 LLM tests pass; ruff, mypy and the boundary check are clean. **A clean full-suite count is still
    owed** — one run died with Postgres when the worker restarted, and the next run's summary line was lost; its only
    failures were the two fixed tests and the known order-dependent flake.
  - **Keys and budget:** founder can allot ₹500–600 for AI during testing. Plan: Groq free tier first, top up only if
    rate limits break runs; no Claude comparison unless gpt-oss fails the evals. Needed from the founder:
    `OPENAI_COMPAT_API_KEY` as an environment secret, and `api.groq.com` allowed under Network access (blocked here).
  - **Local Postgres in this container:** server binaries at `/usr/lib/postgresql/16/bin`, data in
    `/var/lib/postgresql/cdmvp`, run as user `postgres` on 127.0.0.1:5432 with trust auth;
    `TEST_PG_SUPERUSER_URL=postgresql://postgres@127.0.0.1:5432/postgres`. It stops when the session worker restarts.
  - **Languages:** the planner schema lists only `en/hi/hinglish` and scripts `latin/devanagari`; Tamil, Tanglish and
    other Indian languages need those widened plus eval scenarios. Deferred by the founder.
  - **Known issue (pre-existing, found 2026-11-02):** running `tests/test_conversation_flow.py` *before* `tests/evals` in one
    pytest process makes two eval scenarios (`bargain-below-floor`, `order-flow`) get no reply at their fourth step. Proven
    pre-existing: the same two fail identically with the reply-shape change stashed. The normal full-suite order runs
    `tests/evals` first, so CI does not see it; evals alone pass 32/32. Likely leftover state from the conversation-flow
    tests starving a later step. Reproduce: `pytest tests/test_conversation_flow.py tests/evals`.
  - **RESOLVED same day (see the commit 'Prove shop isolation on every table'):** in `tests/test_tenant_isolation.py`, 11 of the
    tables in `TENANT_TABLES` get no rows from `make_tenant` (style_examples, price_floors, offers, turns, negotiations,
    deals, handoffs, knowledge_gaps, owner_messages, config_proposals, audit_log), so the cross-tenant read test passes
    without proving anything for them, and newer shop tables (support, billing) are not listed at all. Row-level security
    is very likely in place, but INV-5 requires it proven per table.
  - **Fixed (2026-11-02):** the long-standing "flaky" raw-log tests (`test_gateway_raw_log.py`) were order-dependent test
    bugs, not product bugs: one scanned every stored webhook and crashed on rows other tests had inserted without a raw
    body; the other counted every rejected webhook in the session. Each now reads only its own rows.
  - **The component split (ADR 0025), 2026-11-02.** Web, gateway and agent no longer import each other (boundary rule B9, baseline
    now empty): sign-in codes, number connect, owner alerts and the reading of Meta's webhook each go through a request or event the
    other side answers (migrations 0017-0020; `gateway_wait.py`; `channel_events` mailbox; new queue `conversation.events`). One
    scheduler per component (`SCHEDULER_COMPONENT`). The agent's workers no longer need the Meta master key (`SENDING_QUEUES` is
    just the gateway's send queue); the web API holds no Meta secret; the gateway and web parts are no longer asked for a model key.
    Deploy: `deploy/db|web|gateway|agent` with their own `.env.example`, `scripts/stack.sh|ps1`, `deploy/README.md`, and
    `tests/test_deploy_files.py` (reads the compose files as data, no Docker needed). **Not verified:** no image was built or started
    (no Docker daemon); the compose files only pass `docker compose config` and the tests above.
    **Still open from ADR 0025:** per-component database roles; the web API still holds the master key for the operator's manual
    pilot-number registration; the platform-alert WhatsApp copy (Critical only) in the web scheduler still uses the platform token. The queue-stats API test now expects five queues (new `conversation.events`).
  - **Platform alerts by email (ADR 0029), 2026-11-02.** Four severities (low, medium, high, critical; migration 0021). Every
    component raises through `salesai/alerts.py::raise_alert`: one open alert per cause with an occurrence count. Critical goes
    out at once and every 30 minutes until resolved; High at once with a 30-minute cooldown and only on news; Medium is an
    hourly rollup; Low a daily digest; Critical and High get one recovery notice. Email is SMTP behind the sink port (no new
    dependency), settings `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`, `SMTP_*`, intervals `ALERT_*_MINUTES`; documented in both
    `.env.example` files; the SMTP password is held by the web scheduler only (deploy test). Never to subscribers: proven by a
    behaviour test and by a test that fails if the alerting code touches any table but the alert table and a shop's name.
    `tests/test_alert_email.py` (20 tests, real SMTP exchange against `tests/fake_smtp.py`). Operator console shows the new
    severities and "happened N times". **Fixed along the way:** `tests/test_deploy_files.py` failed inside the full suite (not alone)
    because other tests leave settings in the process environment, which filled in what a compose file left out; each service is now
    built from a clean environment. **Not built:** automatic resolution when a cause clears; cost-spike and eval-drift rules (admin plane).
  - **Admin plane A and B (ADR 0028), 2026-11-03.** Role `admin` is its own application: sign-in is a WhatsApp code then an authenticator
    code (RFC 6238, stdlib), codes work once, five wrong ones lock 15 minutes, 30-minute sessions with no refresh, tokens signed with
    `ADMIN_JWT_SECRET` which the public app does not hold, optional `ADMIN_ALLOWED_IPS`, enrolment by `python -m salesai admin-enroll`. The
    shop app has no admin routes and the admin app no shop routes (tested); the public sign-in no longer issues operator sessions; the web
    API no longer holds the master key. Four boards (`/api/v1/operator/boards/{sales,growth,money,health}`): aggregates only, `null` with a
    reason for what is not measured. Break-glass: reading conversations needs a time-boxed grant with a reason, logged on the platform trail
    and shown to the shop owner (`GET /business/platform-access`); impersonation needs a reason too. **Not built yet:** the admin frontend,
    `deploy/admin`, passkeys (TOTP accepted by the ADR), recovery codes (re-enrolment on the server is the recovery).
  - **Restart tolerance (ADR 0035), 2026-11-03.** Short renewed leases, fencing tokens (migration 0024), release-on-stop without using an
    attempt, a recovery sweep from the database's own state on the agent scheduler (migration 0025), and a sender that refuses to send a
    message that is no longer queued. Shared queue suite passes on Postgres and Redis. **Not built yet:** a gateway-side sweep for
    webhook rows received but not processed (the raw log keeps them; replay exists in the console).
  - **Agent audit, typing, timers, daily summaries (2026-11-03).** `agent_runs` (ADR 0037), the typing effect (read ticks at once, typing while the
    answer is written, model time counts as typing time), the stale-turn guard before the writer, daily summaries removed (ADR 0040), and the agent's
    clock (ADR 0038): follow-back and owner take-over timers, rules in `timer_rules.py`, re-planned from the database on every change, fired by the agent
    scheduler every pass, repaired by the recovery sweep. **Open:** rules-before-models tiering (ADR 0039), turn-detection accuracy work and its benchmark
    (ADR 0036), customer-style adaptation, the separate admin console front end, and a full-suite run on all of the above.
  - **Turn detection and rules-before-models (ADR 0036, 0039), 2026-11-03.** Turn detection measured on a 53-burst labelled corpus: starts before the
    customer finished 15.1 % to 3.8 %, mean wait 3.27 s to 1.62 s. `TieredPlanner` puts the deterministic planner in front of the language model for a
    conservative allow-list (safety cases, greetings, thanks, plain shop facts, an exactly named product), and the optional model check is skipped for replies
    that state nothing risky. Full suite green on the commit before these two (snapshot run, exit 0).
  - **Chat-native adaptation (ADR 0041), 2026-11-03.** `customers.profile` was never written; now the customer's style (register very casual to formal, language and
    script mix, length, emoji, how they address the shop) is worked out by rules from their own messages, handed to the writer (prompt v4, `reply_hint`), kept on
    the customer, and enforced by a deterministic voice check (no support-desk phrases, no document formatting, no emoji for people who use none, not far longer
    than they write). Five voice scenarios and an every-scenario invariant added to the evals. Real-model quality is only measurable with a key: on the go-live checklist.
  - **The founder's console as its own app (ADR 0028 part C), 2026-11-03.** `frontend/admin` (own build, own image, own strict web server, own deploy part `deploy/admin`, own `.env`):
    sign-in with a WhatsApp code and an authenticator code, a session clock with extend and sign-out, the four boards, the agent audit (summary, filterable runs, per-run timeline),
    "Read a chat" (recorded, time-limited), and the existing shops, alerts, support, queues and billing screens moved over. The owner app lost every console route and screen;
    tests enforce the separation (static import test, bundle check, deploy-file tests). Operating guide: `docs/ADMIN_CONSOLE.md`. **Not built:** passkeys; "sign in as a shop" in the UI.
    **Noticed, since fixed:** the pricing page and `plans.json` showed an "each extra conversation" column that contradicted ADR 0032.
  - **Overage charging removed (ADR 0032), 2026-11-03.** Founder decision: plans are caps, never a per-conversation charge. Renewal invoices now carry the plan line only (a test puts 520
    conversations on a 500 plan and checks the invoice); `overage_paise` is gone from the plan catalogue, `plans.json`, the usage API (`extra_conversations`, `overage_estimate_paise`) and the generated client;
    the pricing page shows "conversations each month" with no extra-conversation price; the billing screen says there is no extra charge and shows a plain note from 80% of the count
    (at the count: the assistant keeps answering, upgrade for room). **Still to build (ADR 0032):** the early WhatsApp warning to the owner and the short grace period at the cap.
  - **Usage warnings (ADR 0032), 2026-11-03.** The owner is told once at 80% and once at 100% of the plan's conversation count, in their WhatsApp, with no mention of any charge
    (migration 0029 `usage_notices`, 0030 allows the new owner-message purpose; template `usage_warning` needs Meta approval, now on the go-live list). Tests: `test_usage_warnings.py` (thresholds,
    once only, jump past the cap, new period, pilots and lapsed shops, one shop's owner only) and the shop-isolation test covers the new table.
    **Also this stretch:** the turn-detection wiring test and three pricing property tests no longer depend on other tests' data or on filtered random inputs (they failed
    occasionally in the full run). **Open question for the founder:** what happens after the cap (keep answering vs pause after a grace); until decided it keeps answering.
  - **Pricing setup as two questions (ADR 0033), 2026-11-03.** New products ask "What is the price?" (typed as you would say it) and "Can the assistant give a discount?" (No / A little / Down to a lowest price);
    the mode, steps, rounding and the "a little" lowest price are derived (up to 5% off, one constant); everything else is under "More options" and unchanged. 64 catalogue unit tests, and two browser tests passed against a
    local stack (`frontend/e2e/pricing-safety.spec.ts`). **Not built:** the shop-wide default asked at onboarding; multi-variant products still use the full editor.
    **Noticed, not caused by this:** these browser specs fail on the code before this change too, so the e2e suite needs a pass: `forms.spec.ts` (label clashes with the (i) tips, e.g. `getByLabel('Offer name')`
    matches two elements; the "* required" note; the delete-button name) and the "Saved privately" check in `pricing-safety.spec.ts` against the demo product.
  - **Browser specs repaired, 2026-11-03.** Ran the browser suite against a local stack (Postgres, the API, the dev server; see `docs/RUNNING.md` section 2). Fixed in the specs, not the app: `forms.spec.ts` (6/6 pass),
    `auth.spec.ts` (4/4: time-based greeting, sign-out asks first, a number with no shop goes to set-up), the sign-up and settings specs. Cause of most failures: `getByLabel('Offer name')` also matches the (i) tip button
    "More about Offer name"; use `getByRole('textbox', { name })` or the placeholder. **Still failing because the screens changed since the specs were written** (each needs a rewrite against the current flow, not a patch):
    `owner.spec.ts` "offers can be created, paused and deleted", "catalog: cards and grid views, a confirmed on/off switch…", "products: edit and delete on every item…", "offers work like products…" (per-card on/off switches are gone;
    hide/show is in the card menu), `playground.spec.ts` (the phone panel selector), and the "Saved privately" check in `pricing-safety.spec.ts` (a card click now opens a read-only view first). `operator.spec.ts` targets the old
    console inside the owner app and must be rewritten for the separate console (it needs an enrolled test admin: WhatsApp code plus authenticator code). Not run here: the mobile project.

