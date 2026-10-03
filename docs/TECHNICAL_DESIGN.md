# Technical Design: principles and constraints

This document states what the system must guarantee and how it must be able to change. It deliberately does not choose languages, frameworks, libraries, schemas in their final form, or algorithms. The coding agent makes those choices, records them, and can revise them.

## How to read this document

Every statement here belongs to one of three levels, marked by its wording.

| Level | Wording | Meaning | Who can change it |
|---|---|---|---|
| Invariant | MUST, MUST NOT | Protects money, customers, tenants or compliance. Violating it is a defect. | Founder only, by editing this document |
| Property | "The system needs…", "required" | A capability or quality the design must provide; the mechanism is open. | Agent chooses the mechanism; founder approves dropping the property |
| Starting point | "For example", "starting point", "one option" | A reasonable first idea offered to save time. Not a decision. | Agent, freely, with a recorded reason |

The product scope and requirements live in the PRD; system shape lives in the HLD. Where the HLD names a specific tool (a database, a queue product, a proxy), read it as a starting point unless a section here makes it an invariant.

This is an MVP, but not a throwaway: v1 ships the smallest feature set from the PRD, built on seams that let later versions scale and change without rewrites (see Built for change).

## Non-negotiable invariants

These are the only fixed rules in this document. Every design the agent chooses MUST preserve all of them, and each MUST have automated tests.

| ID | Invariant | Why |
|---|---|---|
| INV-1 | Floor prices MUST NOT appear in LLM context, API responses, logs, analytics or exports. Only the pricing component reads them. | A leaked floor destroys the owner's negotiating position |
| INV-2 | Every price, discount or offer value stated to a customer MUST originate from the pricing component in that same turn, and MUST be checked before sending. | The AI must never invent a number |
| INV-3 | No price below an item's floor, unless the owner explicitly allowed a specific offer to cross it. | Owner's money |
| INV-4 | on_request items MUST NOT receive a price from the AI. | Owner's choice |
| INV-5 | Tenant data MUST be isolated by a mechanism the database enforces, not only application code. | One bug must not expose another business |
| INV-6 | An inbound customer message MUST be durably stored before the webhook is acknowledged, and MUST NOT be lost or processed twice with side effects. | No silent misses, no duplicate replies |
| INV-7 | A reply planned for an older state of the conversation MUST NOT be sent after a newer customer message arrives. | No answers to outdated questions |
| INV-8 | Free-form messages MUST NOT be sent outside Meta's 24-hour window; the check happens at send time. | Platform rule (CR-2) |
| INV-9 | The AI MUST NOT claim to be human when sincerely asked, MUST NOT invent urgency or product claims, and MUST stay scoped to the business. | CR-1, CR-6, CR-7 |
| INV-10 | Messages to opted-out customers, personal contacts, or conversations where the AI is paused MUST NOT be sent by the AI. | Owner control, CR-3 |
| INV-11 | Owner access tokens MUST be encrypted at rest; no secret appears in code, logs or fixtures. | Account takeover risk |
| INV-12 | Every failure path ends in a reply, a handoff, or an operator alert; nothing fails silently. | A missed lead is lost revenue |

## Built for change: MVP scope with scale-ready seams

v1 builds only what the PRD marks v1, but it is built so that every likely future change is a local change. The test for any v1 design: can the anticipated change below be made by adding or swapping one part, without rewriting others?

| Anticipated change | What v1 must make possible |
|---|---|
| 10× to 200× more tenants and messages | Scale by adding instances and splitting process roles across machines; no shared in-process state; no design that requires one machine |
| Replace the queue mechanism | All async work goes through one internal queue abstraction; producers and consumers never touch the backend directly |
| Switch or mix LLM, transcription or payment vendors | Vendor code sits behind interfaces; vendor choice is configuration, per tenant if needed |
| Add channels (Instagram, web chat, SMS) | Conversation and sales logic never depend on WhatsApp-specific types; WhatsApp is one channel adapter |
| Replace v1 heuristics with learned models (end-of-turn, timing, style) | Each is a swappable component behind an interface, and v1 already records the signals the learned version needs |
| New pricing rules and offer types | Pricing rules are data plus a pure, tested component; new rule types extend it without touching conversation code |
| New product features (follow-ups, payment links, staff routing) | Features subscribe to existing domain events instead of being wired into the core flow |
| Split a hot component into its own service | Components communicate through explicit interfaces and events, never shared internals, so a boundary can become a network boundary |
| Schema evolution | Migrations are versioned, backward compatible across one release, and run separately from application start |
| Frontend redesign or a mobile app | The backend exposes a versioned, documented API; no business logic lives only in the frontend |

**Guardrails against over-building:** do not build the future versions, only leave room for them. No extra services, abstractions or infrastructure without a v1 requirement or a seam from the table above. When a seam costs significant complexity in v1, record the trade-off and choose the simpler option.

## How the coding agent makes and records decisions

The agent owns every technical decision not fixed by an invariant, and records each significant one as an Architecture Decision Record (ADR), a short dated file in `docs/decisions/` stating the context, options considered, the decision and its consequences. The common alternative, decisions living only in code and commit messages, loses the reasoning when the next session or engineer revisits them.

### Decision criteria, in priority order

1. Preserves every invariant, with tests that prove it.
2. Meets the PRD's functional and non-functional requirements.
3. Keeps the seams in Built for change.
4. Simplest option that works for v1; fewest moving parts to operate.
5. Reversible over irreversible: when two options are close, choose the one that is cheaper to undo.
6. Mature, well-documented, widely used technology over novel technology.
7. Lower running cost per conversation (PRD unit-cost target).

### Method

- When options are close or uncertain, run a time-boxed spike or benchmark and cite the result in the ADR.
- Revisit a decision when its stated assumption breaks (for example a measured bottleneck); write a new ADR that supersedes the old one rather than editing history.
- Ask the founder only when a choice would change product behavior, cost a paid commitment, or weaken an invariant. Otherwise decide and proceed.

## Codebase and stack: requirements, not choices

The agent chooses languages, frameworks and libraries; the choice must satisfy these requirements.

### Stack requirements

- Strong, maintained SDKs for the chosen LLM vendors, structured (JSON-schema) outputs, and async I/O.
- Static typing or equivalent checks on domain code.
- API contract generated from or validated against code, so the frontend client cannot drift.
- Runs in containers; no dependency on one cloud vendor's proprietary runtime in v1.
- Hiring and community reality in India: a stack the founder can hire for later.

### Codebase requirements

- Two independent deployables, backend and frontend, in one repository or two; they share only the API contract.
- Backend organized as modules with explicit boundaries (tenants, channels, catalog, pricing, conversations, agent, delivery, sales, handoffs, notifications, media, reporting, billing, or the agent's better split). A module exposes a public interface and the events it publishes; other modules never use its internals.
- Boundaries enforced automatically in CI (an import or dependency rule checker), not only by convention.
- Infrastructure reached only through interfaces (see Contracts).
- One backend build artifact runs in different roles (API, webhook ingress, workers, scheduler) selected by configuration.
- A simulated messaging channel exists so the full product runs and is tested without Meta.

**Starting point (not a decision):** Python with FastAPI and Pydantic for the backend, a TypeScript single-page app for the frontend, Postgres, Docker. Choose differently if the criteria point elsewhere.

## Contracts: ports, events and APIs

Components depend on capabilities, not vendors. The agent designs the exact signatures; these capabilities must exist behind interfaces.

| Interface | Must be able to | v1 implementations |
|---|---|---|
| Messaging channel | Send text, reactions and templates; mark read with typing indicator; download media; report send results | WhatsApp Cloud API, simulator |
| LLM provider | Generate with structured output; report tokens and latency; support fallback | Agent's choice |
| Transcriber | Turn audio into text with language hints (Hindi, English, Hinglish) | Agent's choice, by testing on real audio |
| Queue | Publish with an ordering key, a run-at time and a version; consume with retries and dead-lettering | Agent's choice |
| Blob store | Put and get media by tenant-scoped key | Agent's choice |
| End-of-turn predictor | Estimate whether the customer has finished, and when to check again | Heuristic or small-model version |
| Delivery planner | Turn a reply into timed actions | Distribution-based version |
| Payment provider | Create and manage subscriptions | v2 |

**Domain events.** Components announce facts (for example message received, turn decided, handoff created, deal captured) rather than calling each other to trigger work. Events need a stable envelope with an ID for deduplication, tenant ID, ordering key, version where relevant, and a schema version so producers and consumers can be deployed independently. The event catalog is the agent's to define and document.

### External API (for the frontend and future clients)

- Versioned (for example `/api/v1`), documented by a machine-readable contract, with a generated client.
- Tenant derived from authentication, never from a client-supplied ID.
- Consistent error format, pagination, and idempotency for retried writes.
- Floor prices write-only (INV-1).
- Must cover every capability in the PRD's functional requirements: auth, business profile, catalog and pricing, offers, style examples, contacts, conversations with pause and resume, deals, handoffs, knowledge gaps, metrics, live updates, operator functions, and Meta Embedded Signup completion.

**Webhook endpoint:** verify Meta's signature, store the raw event, enqueue, acknowledge; nothing else in the request path (NFR-1, INV-6).

## Data: required properties

`db/schema.sql` is a starting point: a worked example of one design that satisfies these properties. The agent may restructure it, provided every property below still holds.

- **Tenant isolation enforced by the database (INV-5).** The reference uses row-level security plus composite foreign keys; any mechanism with equal or stronger guarantees is acceptable. Automated tests attempt cross-tenant access on every table.
- **Private pricing data physically separated** so the code path that builds LLM context cannot read it (INV-1).
- **Idempotent ingestion:** a uniqueness guarantee on Meta's message ID (INV-6).
- **Conversation versioning:** some monotonic marker per conversation that lets any planned action detect it is stale (INV-7).
- **Every price has one source of truth** (in the reference, the variant), and money uses exact decimal types with a currency.
- **Auditability:** pricing and settings changes recorded with actor and time; every AI turn recorded with its decision, model, prompt version, tokens, cost and latency (FR-OP-2).
- **Learning signals retained from day one:** message timestamps (sent, delivered, read), owner echo timings, customer inter-message gaps, turn outcomes.
- **Event integrity:** a state change and the event announcing it either both happen or neither (for example a transactional outbox).
- **Growth-ready:** high-volume tables can later be partitioned by time and the whole dataset sharded by tenant without changing the domain model; every query is already scoped by tenant.
- **Lifecycle:** per-tenant export and hard delete; retention periods per data type as set in the HLD.
- **Migrations** versioned, reviewed, backward compatible with the previous release, and run as a separate step.

## Async processing: required properties

All work after the webhook acknowledgment is asynchronous. The agent picks the mechanism; it must provide every property below, and the same test suite must pass when the backend is swapped.

| Property | Why |
|---|---|
| Durable: an accepted job survives a process or machine restart | INV-6, INV-12 |
| Per-key ordering: jobs for one conversation never run concurrently; different conversations run in parallel | Correct turn handling at any scale (NFR-4) |
| Delayed execution: a job can be scheduled for a future time; no worker blocks waiting | Turn-taking waits and human-like delays without wasting capacity |
| Supersession: jobs planned for an older conversation version are discarded | INV-7 |
| Retries with backoff, then a dead-letter destination with the error | INV-12 |
| Idempotent handlers: reprocessing a job has no duplicate side effects | Safe retries |
| Per-tenant fairness and limits: one busy tenant cannot starve others | Multi-tenant SaaS |
| Per-number rate limiting for outbound sends | Meta throughput limits |
| Observable: depth, age and failure counts per queue | Autoscaling and alerts |
| Backend swappable by configuration | Scale path in the HLD |

**Starting point (not a decision):** a database-backed queue for v1, because it avoids running extra infrastructure at pilot scale and keeps job and state changes in one transaction; a dedicated broker once measured load requires it.

## Conversation engine: required behavior

The conversation engine decides when the customer has finished, triggers the agent, and handles interruptions. Behavior is specified here; states, data structures and algorithms are the agent's design.

### Required behavior

1. Several consecutive customer messages that form one request get one response (FR-CV-2).
2. The system never waits indefinitely: a configurable maximum wait bounds every decision.
3. A new customer message at any point cancels anything planned but not yet sent, and the response is replanned with all unanswered messages (INV-7).
4. Voice notes are transcribed before the turn is decided.
5. When the owner replies manually, the AI pauses in that conversation for a configurable period (FR-CV-9); owner stop and start commands pause the whole business.
6. Personal contacts, opted-out customers and paused conversations are recorded but never answered by the AI (INV-10).
7. The 24-hour window is tracked and enforced at send time (INV-8).

### Design freedom

- The end-of-turn decision sits behind its interface. A v1 starting point: combine a small model's judgment of whether the text is complete with this customer's historical gaps between messages. The agent may use any approach that passes the conversation evals.
- An explicit state machine is the suggested way to make interruption handling correct and testable; its exact states are the agent's choice.
- Thresholds and waits are configuration, tunable per business, never constants buried in code.

## Pricing engine: required properties

The pricing engine is the only component that turns owner pricing rules into numbers a customer may hear. It must be deterministic and free of I/O, so it can be tested exhaustively.

### Required properties

Each is covered by property-based tests over generated policies and negotiation sequences.

- Never yields a value below floor, except through an offer the owner explicitly allowed to cross it (INV-3).
- Never yields any number for an on_request item (INV-4); routes those to handoff.
- Respects each disclosure mode from the PRD: fixed, range, starts_from, after_qualifying, on_request.
- Negotiates only when the owner enabled both negotiability and AI negotiation for that item.
- Concedes gradually rather than jumping to the floor; can require something in return (quantity, advance payment, repeat customer) before a concession.
- Never offers less than the customer has already offered.
- When a customer insists below floor, holds once with no new number, then hands off.
- Applies only offers that are active, unexpired and whose conditions are met.
- Returns the exact set of values it issued, so the reply check can enforce INV-2.
- New rule and offer types can be added without changing callers.

**Starting point (not a decision):** concession steps evenly spaced between list price and floor, rounded to a per-business rounding rule. The agent may choose a better schedule (for example larger first concessions) if evals and pilot data support it.

## LLM orchestration: required properties

The LLM decides what to do and how to say it; code owns every number and every rule. The pipeline's shape (how many model calls, which models, prompt design) is the agent's to design and to improve through evals.

### Required properties

- Model output that drives actions is structured and schema-validated, never parsed from free text.
- The model cannot change data directly; it proposes actions that code validates and executes.
- Any number in a reply comes from the pricing engine's output for that turn (INV-2); the model never receives floor prices (INV-1).
- Every outbound reply passes automated checks before sending: issued prices only, business scope, no human claim when asked, no unbacked urgency, language and script matching the customer. A failed check leads to one regeneration, then a safe holding message plus handoff.
- Context is assembled from tenant data only: business profile, relevant catalog, conversation history, lead and negotiation state, customer style, owner voice examples. It must scale to large catalogs and long conversations (retrieval and summarization when needed).
- Prompts are versioned files; each turn records the prompt versions, models, tokens, cost and latency.
- Model and vendor routing is configuration, with a fallback for provider failure.
- Cost per AI conversation is measured and reported per tenant; the PRD target is a ceiling to design toward.
- Any change to prompts, models or the pipeline must pass the conversation evaluation suite before merge.

**Starting point (not a decision):** a planner call that returns intent, lead stage and a proposed action, the pricing engine executing any price request, and a separate writer call that phrases the reply using only cleared values, with a small model for cheap checks. The agent may merge or split calls if evals show equal safety at lower cost or latency.

## Human-like delivery: required properties

Replies are paced like a person writing, and the pacing must be learnable per business rather than fixed in code.

### Required properties

- Read receipts, typing indicators, multi-part replies and the gaps between parts are scheduled actions, not immediate.
- Timing comes from parameters stored per business (distributions or any learnable form), never from constants in code, so two replies never share an identical rhythm and each business can be tuned separately.
- A configurable cap bounds total delay, so pacing never costs a sale.
- Every scheduled send re-checks the window, AI pause, opt-out and conversation version at execution time (INV-7, INV-8, INV-10).
- Business-hours behavior is a business setting (reply normally, slower, or wait for opening hours within the window).
- The signals needed to learn timing later are stored from day one: owner reply and read timings from echoes, customer read times from status updates, customer gaps between messages, and outcomes.

**Starting point (not a decision):** log-normal priors for read delay, typing time per character and gaps between parts, replaced per business as owner data accumulates; a bandit-style tuner in v2 that optimizes engagement and conversions while penalizing blocks and "are you a bot?" questions.

## Frontend: required properties

The frontend is a separately deployable client of the API; framework and design system are the agent's choice.

- Deploys independently: on the same machine as the backend or on its own host, with the API location supplied at runtime, not baked into the build.
- Uses only the documented API, through a client generated from the API contract.
- Contains no business rules beyond input validation; pricing logic, limits and permissions live in the backend.
- Mobile-first, because owners work from phones; usable on low-end Android devices and slow networks.
- English and Hindi from day one, with room for more Indian languages.
- Floor price fields are write-only and visible only to the owner role (INV-1).
- Covers the PRD's owner capabilities (onboarding and Embedded Signup, dashboard, conversations with live updates, pipeline, catalog and pricing, offers, voice examples, settings) and the operator console; includes a developer simulator view for chatting as a customer.
- Owners with low literacy rely mainly on WhatsApp (voice setup, voice summaries); the web app must not be required for any core owner task.

## Configuration, deployment and testing: required properties

### Configuration

- Everything that differs between environments or deployments (process role, queues consumed, connection strings, vendors, models, safety bounds) comes from the environment, validated at startup; invalid configuration stops the process.
- Everything that differs between tenants (sales style, timing parameters, limits, handoff triggers) is data in the database.
- Secrets come from environment variables or a secret store only (INV-11).

### Deployment

- The same build artifacts run in both HLD topologies: all on one machine, or split across machines. Moving between them is configuration and infrastructure, never code.
- Every process exposes liveness and readiness checks.
- Migrations run as a separate step before rollout; old and new versions of workers can run side by side during a deploy.
- Development, staging (Meta test number) and production environments; production deploys need explicit approval.

### Testing (gates in CI)

| Gate | Proves |
|---|---|
| Unit and property tests | Module logic; pricing invariants over generated cases |
| Integration tests against real infrastructure in containers | Queue properties, data properties, supersession |
| Tenant isolation tests | INV-5 on every table and view |
| Contract tests on recorded Meta payloads | Every webhook event type is handled |
| End-to-end tests with the simulator channel | Batching, interrupts, pacing, handoffs |
| Conversation evaluation suite | Business behavior and invariants with real models; required for prompt, model or pipeline changes |
| Load tests | NFR-1, NFR-2, NFR-5 |
| Module boundary check | Seams from Built for change stay intact |

The evaluation suite starts with scenarios across Hindi, English and Hinglish and several business types, and grows with every real failure found in pilots. Its size and graders are the agent's design.

## Open technical decisions

These are the agent's to make, each recorded as an ADR. Starting points are offered only to save time.

| Decision | Must satisfy | Starting point | How to decide |
|---|---|---|---|
| Backend language and framework | Stack requirements; LLM SDK maturity | Python, FastAPI | Criteria in priority order |
| Frontend framework | Static deploy, generated API client, mobile-first | TypeScript SPA (React, Vite) | Criteria |
| Database | Database-enforced tenant isolation; exact decimals; transactions | Postgres | Criteria |
| Queue mechanism | All async properties | Database-backed in v1 | Same test suite on candidates |
| Final data model | All data properties | db/schema.sql | Model the PRD flows; keep properties |
| Event catalog and envelope | Stable IDs, tenant, ordering key, schema version | — | Derived from module interactions |
| LLM vendors and models | Structured output, Hindi and Hinglish quality, cost target | — | Conversation evals plus cost per conversation |
| Pipeline shape (calls per turn) | All LLM properties | Planner, engine, writer, checks | Evals: safety first, then cost and latency |
| Transcription vendor | Hindi, English and Hinglish accuracy | Test several | Accuracy on recorded pilot audio |
| End-of-turn method | Required conversation behavior | Small model plus customer gap history | Evals on fragmented-message scenarios |
| Concession schedule | Pricing properties | Even steps to floor | Evals now, pilot outcomes later |
| Owner dashboard authentication | Secure, works for low-literacy owners | One-time codes over WhatsApp | Usability with pilot owners |
| Hosting provider and region | Low latency to Indian users, DPDP obligations, cost | An Indian region | Cost and latency measurement |
| Observability tooling | Tenant-tagged logs, metrics, traces | Vendor-neutral instrumentation | Criteria |
