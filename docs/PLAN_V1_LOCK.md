# V1 Launch Plan (locked)

_Locked 2026-11-02. Supersedes the "Next steps" list in PROGRESS.md section 7._

This plan closes out v1. The code is roughly 90% built (PROGRESS.md sections 3–4); what remains is
verification, four cross-cutting tracks (security, platform plane, auditing, AI economics) and one
restructure. Every technical decision below is **decided** and recorded in ADRs 0025–0030. Decisions
left to the founder are in section 12, each with a default already in force, so nothing here is blocked.

## 1. What the product is, in one line

The AI answers a shop's WhatsApp leads, gives real product detail, and persuades toward a commitment,
inside pricing rules the owner sets. **Goal: more sales. Direction: handle WhatsApp leads more
autonomously.** The ordered enhancements after v1 are **order placement → payment handling → voice
calls**; none is built now, and section 10 names the seam each one will land on.

## 2. The three things that decide this product

In priority order. Everything in this plan serves one of them.

1. **Meta.** We do not own the channel. Meta's terms, approval queue, pricing and quality rules can
   stop or reprice the product at any time. The code is ready; Meta approval is entirely external and
   is the critical path (section 3).
2. **Trust.** One unauthorized price loses the shop. The pricing engine and INV-1/2/3 exist for this.
3. **Unit cost.** Each conversation costs real money in two places now — the model and, since
   1 October 2026, Meta's per-message fee (section 4).

For the AI specifically the founder set the order **security → UX (answer quality first, then latency)
→ cost**. ADR 0027 applies it: never buy cost savings with answer quality; take cost from caching and
token discipline, which cost no quality at all.

## 3. Meta: current rules and what they change

> **Verification status.** Meta's own domains (`developers.facebook.com`, `whatsapp.com`) are blocked by
> this session's network policy, so everything here comes from secondary sources dated 2026 and must be
> re-read against Meta's own pages before pilot. Treat every figure as indicative. To let a future
> session check them directly, add those two hosts under Network access in the environment settings
> (cloud environment menu in the title bar → Edit).

| Rule (as of Nov 2026) | Status | What it means for us |
|---|---|---|
| **AI Providers clause**, in force 15 Jan 2026 | Live | Bars AI providers whose AI *is* the product, but expressly allows a business to "retain an AI Provider as your Third Party Service Provider". Our posture must be: each shop is the customer, we are its Tech Provider, the AI serves that shop's own sales. CR-1 already forbids "chat with AI" positioning — keep it out of marketing, app review and the app surface. |
| **AI training ban** in the same clause | Live | WhatsApp data must not be used to create, develop, train or improve any AI model — **even anonymised or aggregated** — except fine-tuning exclusive to that one business. This collides with the documented v2 plan for learned end-of-turn, timing and style models and bandit tuning. See section 9 and the proposed INV-13. |
| **Per-message billing**, since 1 Jul 2025 | Live | Every delivered message is billed, not every conversation. Multi-part replies cost N×. |
| **Service messages billed**, since 1 Oct 2026 | Live, one month old | 1,000 free service messages per business phone number per calendar month, then the utility rate. Our replies are service messages. This is new and the whole pricing page was written before it. |
| India rates | Indicative | Service/utility ≈ ₹0.115 per message; marketing ≈ ₹0.863. |
| **Embedded Signup v2 deprecated 15 Oct 2026** | Deadline passed | Must be on v4. Coexistence needs `featureType: whatsapp_business_app_onboarding` and `sessionInfoVersion: 3` — **already correct** in `frontend/src/features/settings/signup.ts`. `graph_version` is configuration; pin it to a current version. |
| Coexistence prerequisites | Gate | Solution Partner or Tech Provider status, WhatsApp Business app ≥ 2.24.17, a webhook that reliably processes events, Embedded Signup with session logging. We meet the technical ones; partner status is the gate. |
| New coexistence webhooks, Apr 2026 | **Gap** | `account_offboarded` and `account_reconnected` are not in our synthetic fixtures. They are exactly the FR-ON-4 disconnect signals. Add both. |
| Messaging limits / quality rating | Mostly not binding | Limits cap business-*initiated* messages to unique users. v1 is inbound-only, so limits barely apply; **quality rating does** — blocks above roughly 2–3% drag it down. Each shop has its own number under coexistence, so quality is per-tenant; our app's aggregate conduct still affects our standing as provider. |

**Three concrete changes this forces**, all in the build queue:

1. Count and attribute every Meta message per tenant, with the 1,000-per-number monthly free tier
   modelled (ADR 0026). Nothing counts Meta messages today.
2. Budget reply parts, because parts are now money (ADR 0026).
3. Handle the two new coexistence webhooks and re-record all fixtures from a real test number.

**The critical path is Meta Tech Provider approval, and it has not started.** It gates self-serve
onboarding for every non-technical shop, which is the whole distribution model. Nothing in the
codebase shortens it. Start it this week; run pilots on the platform's own WhatsApp Business Account
(FR-ON-3) meanwhile.

## 4. Unit economics (the number that sets pricing)

Model prices are current (Nov 2026): Haiku 5.5 $0.10/$0.50 per MTok, Sonnet 5.5 $2/$10, Opus 5.5
$4/$20; cache reads $0.20 on both Sonnet 5.5 and Opus 5.5.

Two findings from the code:

- **Prompt caching is not implemented** — no `cache_control` anywhere in the agent. Every turn re-sends
  the business profile, catalog slice, style examples and system prompt at full input price. This is the
  single largest cost item and removing it costs no answer quality.
- **The cheap-model slot still points at `claude-haiku-4-5`** ($1/$5). `claude-haiku-5-5` is $0.10/$0.50
  — ten times cheaper for the same slot.

Illustrative, to be replaced by measurement (ADR 0027):

| Per AI turn | Uncached | With caching |
|---|---|---|
| Planner + writer on Sonnet 5.5, ~4k stable prefix + ~500 fresh + ~200 out | ≈ $0.022 | ≈ **$0.008** |
| Checks on Haiku 5.5 | negligible | negligible |

At three turns per conversation that is roughly **₹2.1 per conversation** in model cost, plus **≈₹0.5**
in Meta fees once a shop is past its free 1,000 — call it **₹2.6 variable per conversation**.

**Consequence for pricing, and it is uncomfortable:** at the placeholder ₹2,499 tier, 900 conversations
a month would cost about ₹2,330 in variable cost. Included-conversation counts have to sit in the **low
hundreds**, not thousands, for a sane gross margin. The counter-weight is that Meta's 1,000 free
messages per number per month means **small shops are almost free to serve**, so a cheap entry tier is
genuinely profitable and land-and-expand works. Plan prices stay placeholders until the measured
numbers from Phase 2 land; the shape of the question is in section 12.

## 5. Architecture (locked)

Three deployable components plus a migration job, as drawn in the component map. Full decision in
**ADR 0025**.

| Component | Holds | Never |
|---|---|---|
| **web** | frontend + its REST backend + web scheduler (billing, trials, cleanup) | touches Meta; calls the agent |
| **gateway** | receive / inbound / send; the only holder of Meta tokens | runs sales logic or calls the model |
| **agent** | turn detection, planner, pricing engine, writer, checks, tools, follow-up scheduler | talks to web; reads floor prices into model context |
| **migrations** | own image, only holder of schema-owner credentials, runs first | runs inside any live component |

Decisions I made on the three questions left open in the component map:

- **Owner alerts and the daily summary go to WhatsApp as well as the app.** FR-HO-1 and FR-RP-1 require
  it; it is not mine to drop. They are template messages, so they are billed and need Meta approval —
  batch handoff alerts and cap them per hour so a busy shop does not get spammed or billed hard.
- **web never calls the gateway directly.** It writes a request row and the gateway acts on it. The
  earlier draft allowed a direct call for sign-in codes, number connect and owner replies; a database
  mailbox is better because it keeps web permanently free of Meta tokens, which is what ADR 0020 bought.
  The sign-in code pays a sub-second delay for that, which is acceptable.
- **Two schedulers**, one per component, as proposed.

Also locked: **ADR 0021 step 1** (group the 37 tables into `platform` / `shop` / `convo` / `infra`
schemas) happens **before** the pilot. It is a rename with no data copy today and gets more expensive
with every table added.

## 6. Security (locked)

The existing posture is strong and stays: database-enforced tenancy (RLS + composite foreign keys),
floors behind their own role and `SECURITY DEFINER` functions, Meta tokens reachable only by the
`send` role, per-role secrets, signed webhooks, encrypted tokens.

Added by this plan:

1. **Admin plane isolation** (ADR 0028). The founder console moves to its own origin
   (`admin.<domain>`) served by its own API role, not reachable from the public API. Second factor
   (passkey or TOTP) mandatory, short sessions, strict CSP, no third-party scripts, its own read-mostly
   database role.
2. **Platform staff cannot read customer conversations by default.** Aggregates only. Reading a real
   conversation is break-glass: a stated reason, time-boxed, written to the append-only audit log, and
   visible in the shop's own audit view. This is both a DPDP posture and the right trust position for a
   product that sits inside people's private chats.
3. **Prompt injection is treated as an attack surface.** Customer messages are untrusted input that
   reaches the model. INV-1 already protects floors structurally by never putting them in context —
   that is the right defence and it stays. The backstop is the existing send-time reply checks. What is
   missing is adversarial coverage in the evals: floor-extraction attempts, instruction override,
   impersonating the owner, tool-abuse phrasing (ADR 0027).
4. **Secret hygiene gate in CI** — fail on any secret-shaped string in source, fixtures or prompts.
5. **`OTP_ACCEPT_ANY` becomes impossible outside dev.** It is refused in `ENV=production` today; make
   it refuse anywhere the host is internet-reachable, because any staging box with real data is exposed.

## 7. Platform plane: analytics, monitoring, alerts (locked)

New, founder-facing, separate from the existing per-tenant operator console. ADRs 0028 and 0029.

**One dashboard, four boards**, because the question "how is my SaaS doing" is four questions:

| Board | Leads with |
|---|---|
| **Sales outcome** (the north star) | autonomous handling %, lead→commitment rate, owner-confirmed wins, bot-suspicion rate, wrong-fact and unauthorized-price counts (must read 0) |
| **Growth** | activation funnel — signed up → number connected → first AI reply → first commitment → paid; trial→paid conversion; churn |
| **Money** | MRR, ARPU, variable cost per tenant split model vs Meta, **gross margin per tenant**, overage |
| **Health** | webhook p99, queue depth and oldest-job age, dead letters, LLM error and fallback rate, send failures, eval pass rate, Meta quality rating and number status per tenant |

**Monitoring and auditing live on the same admin origin**, not a third one. Splitting them would mean a
second auth surface and a second thing to secure for no isolation gain — the data is equally sensitive
and the audience is one person.

**Email alerts go to the founder only, never to subscribers** (ADR 0029), in four severities: **Critical**
(immediate, repeats until resolved — e.g. an unauthorized price), **High** (immediate — e.g. a shop
disconnected from Meta), **Medium** (hourly rollup — e.g. rising send failures) and **Low** (daily digest). Alerts are grouped and
deduplicated by cause, with per-rule cooldowns — a solo founder who gets 200 emails stops reading them,
so alert fatigue is a correctness requirement, not a nicety. Subscriber-facing alerts stay where they
are, on WhatsApp and in-app.

## 8. Auditing, governance, DPDP (locked)

ADR 0030.

- **Append-only audit log.** `audit_log` exists with actor, action, entity and detail. Revoke UPDATE and
  DELETE from every application role so history cannot be rewritten; only the migration role may alter
  it. A tamper-evident hash chain is recorded as a deferred option, not built.
- **Audit coverage required** on pricing and settings changes (already partly there via
  `catalog/repo.py`), every admin action, every break-glass read, consent and opt-out changes, and
  tenant export and delete.
- **Retention schedule per data type**, enforced by the existing scheduler: raw webhook payloads
  (`RAW_WEBHOOK_RETENTION_DAYS`), messages, turn records, audit log, support attachments. Written down,
  configurable, tested.
- **DPDP posture.** The Rules were notified 13 Nov 2025; the core obligations — consent and notice,
  security safeguards, 72-hour breach reporting, retention and erasure, data-principal rights,
  cross-border conditions — take effect **13 May 2027**. The Consent Manager registration on 13 Nov 2026
  does not apply to us. Each shop is the Data Fiduciary for its customers and we are its Data Processor,
  so we need a Data Processing Agreement in the legal pages, not just a privacy policy. Build to the May
  2027 obligations now while it is cheap; per-tenant export and hard delete already exist.
- **Breach runbook** with the 72-hour clock, written before pilot.
- **Housekeeping:** two ADRs share the number 0020 (`channel-gateway`, `support-tickets`). Note it in
  `docs/decisions/README.md` rather than renumbering published files. **0024 is reserved** for the
  client-rendered-site ADR that exists only on the founder's machine.

## 9. AI: quality, relevance, latency, cost — in that order

ADR 0027. Applying the founder's ordering.

**Security first.** Floors never enter context (INV-1, structural). Every reply passes send-time checks.
Adversarial eval scenarios added (section 6).

**Then answer quality, which is the product.** The AI's job is to give real detail and persuade.
A genuine gap: **the 32-scenario eval suite proves invariants, not selling ability.** It can pass while
the assistant is safely useless. Add a sales-effectiveness dimension — did it qualify the need, answer
with real catalog fact, handle the objection, advance the lead stage, ask for the commitment, avoid
dead-ending — and grow the suite toward ~80 scenarios across Hindi, English and Hinglish and several
business types. Prompt, model and pipeline changes already gate on evals; that gate now has to measure
both safety and selling.

**Then latency.** NFR-2 allows 8s p95 to first message. Human-like pacing already delays replies
deliberately (up to ~20s), so the customer cannot perceive a slower model inside that envelope.
**Pacing buys latency headroom, which we spend on answer quality** rather than on a weaker model.

**Then cost, taken only where it costs nothing.** In order: implement prompt caching; move the cheap
slot to `claude-haiku-5-5`; keep context disciplined (retrieval over large catalogs rather than dumping
them); only then consider model slotting. Writer stays on a strong model by default; Haiku 5.5 takes
classification, end-of-turn and checks **only where evals prove it safe**. Verify `PRICES_USD` and the
INR rate against published pricing.

**The training ban (section 3) constrains the learning roadmap.** Signals keep being recorded — that is
required and harmless — but any learned model built on WhatsApp data must be **exclusive to the one
shop it came from**. Cross-tenant learned end-of-turn, timing or style models, and cross-tenant bandit
tuning, are off the table while that clause stands. I propose this as **INV-13**; invariants are the
founder's to set, so it is in section 12.

**Per-tenant learning loop (ADR 0031).** Per-tenant is exactly the shape Meta's clause permits, so the
founder's request for the assistant to learn from owner replies is both good product and compliant. The
design point is the one the founder named: an owner answering "no refund" after a handoff may mean no
refunds ever, or only that product, only that customer, or only because of something agreed offline.
So the loop **proposes, never assumes** — it rides the knowledge-gap rail that already exists (FR-HO-2:
owner answer → confirmation → business fact), requires the same answer across distinct conversations
before proposing anything, proposes the **narrowest** scope the evidence supports, and never learns
prices, floors, offers or handoff triggers. Only owner-authored text is a source; a customer claiming
"the shop told me returns are free" must never become a fact. Capture ships early because it changes no
behaviour; proposing ships late, per-tenant and off until the owner opts in.

## 10. Scaling, with triggers not guesses

Measured today: webhook p99 25ms at concurrency 5 (limit 300ms), 414 req/s on one ingress; 240-message
end-to-end run with 0 dead letters; 3M messages is 1.2GB with all reads under 150ms except analytics.
NFR-5 (50 businesses, 5,000 conversations/day) is comfortably met.

| Trigger | Action |
|---|---|
| Ingress sustained above ~300 req/s | add ingress instances (measured saturation point) |
| `convo.messages` past ~50M rows | monthly range partitions by `created_at` (ADR 0021 step 2) |
| Analytics page reads slow again | daily roll-up table (ADR 0021 step 3) |
| One database no longer enough | split `platform`+`shop` from `convo` (ADR 0021 step 4) |
| Queue depth outgrows Postgres | Redis backend already passes the same suite — configuration change |

Seams held open for the enhancement roadmap, none built: **order placement** extends the existing
commitment/deal records; **payment handling** lands on the payment-provider interface (today only
subscriptions use it, customer payments are a different adapter); **voice calls** reuse the channel
adapter and the media seam that ADR 0015 deliberately left in place.

## 11. UX for non-technical shop owners

**This is a first-class requirement, not a polish pass.** The tenant is a business owner fielding a lot of
WhatsApp enquiries, not a technical user. Every surface has to be obvious without explanation, and
anything that needs a manual is a defect.

Three rules follow, and they bind every feature in this plan:

1. **WhatsApp is the primary interface; the web app is optional.** Any task an owner must do can be done
   from the chat they are already in. Already true and kept: coexistence means they keep using their normal
   WhatsApp Business app; setup by voice or text with read-back confirmation (FR-CF-8); daily summary on
   WhatsApp; the web app is required for no core task.
2. **Ask in plain words with numbered choices, never with a form.** The learning loop (ADR 0031) is the
   worked example: one message, one question, answerable with a single digit — "Should I tell other
   customers the same? 1 no refunds on anything, 2 only on sarees, 3 just that one order, 4 don't save
   this." No settings screen, no jargon, no concept the owner has to learn.
3. **Interruptions have a budget.** An owner who is asked something after every handoff stops answering.
   Proposals and non-urgent questions ride the daily summary, rate-limited per tenant; only the most
   urgent thing interrupts.

The frontend is **not** being redesigned. It gets validated and hardened:

- Accessibility pass and a CI gate (whether one exists today is unconfirmed — verify first).
- Low-end Android on throttled CPU and a slow network, measured, not assumed.
- Empty, loading and error states on every screen; every failure says what to do next.
- Keyboard navigation; forms keep the red-asterisk and inline-error behaviour already built.
- The 36 browser specs stay green and grow with each new surface; the new admin plane gets its own.

**The worst offender is pricing setup, and it is now fixed by decision.** An owner faces up to ten
decisions per product (five disclosure modes, list price, range bounds, negotiability, concession steps,
conditions, rounding, plus the floor). A shop with fifty products cannot do that. **ADR 0033** reduces it
to two questions — what is the price, and can the assistant give a discount — infers the mode from what
the owner types instead of making them pick one, derives everything else, asks once at onboarding rather
than per product, and keeps every advanced field behind a collapsed section. The data model, the API and
the pricing engine do not change, so no invariant moves.

One open product question: the owner app is English-only by founder decision, while the target is
non-technical Indian SMBs and the i18n seam is already in place. Section 12.

## 12. Founder decisions outstanding

Each has a default already in force, so the plan proceeds either way.

| # | Decision | Default in force |
|---|---|---|
| 1 | ~~Reply parts vs cost~~ — **decided 2026-11-02, after correcting my own figure.** The spread is ~₹620/month on a busy shop (1 part vs 3), not ₹150. Splitting becomes **adaptive**: one message by default, split only for genuinely long answers, mirroring the customer's own rhythm (ADR 0026). Better UX and lower cost together. | settled |
| 2 | ~~Plan shape~~ — **decided by the founder 2026-11-02:** packages with a conversation **cap**, no per-unit billing and no overage charge; reaching the cap prompts an upgrade. Billing by AI usage ruled out. Market check and provisional caps in **ADR 0032**; final numbers from Phase 2. | settled |
| 3 | **INV-13: no cross-tenant model training.** Meta's clause forbids training on WhatsApp data even aggregated. Make it an invariant with tests, which closes off cross-tenant learned models and bandit tuning? | Treated as binding and designed for; not yet a tested invariant |
| 4 | **ADR 0006.** The last concession step equals the floor and is quoted as a final price. My recommendation changed to **stop just above the floor**: if the final quote always equals the floor, a persistent customer learns the exact number INV-1 exists to protect. | As built |
| 5 | Owner app in Hindi | English only |
| 6 | Still open from PROGRESS.md section 6 | unchanged — legal entity, domain, LLM key, Meta account, pilot shops, GSTIN, payment gateway, default negotiation off |

## 13. Build sequence

Four phases. Phase 0 needs nothing external and starts now; Phases 1 and 2 are gated on Meta and an
API key respectively and can run in parallel with each other.

**Phase 0 — correctness and economics (no external dependency)**
1. Prompt caching, `claude-haiku-5-5` in the cheap slot, verified prices, per-turn cost accounting (ADR 0027).
2. Meta message accounting per tenant with the free-tier model, and the reply-part budget (ADR 0026).
3. Component contract: table ownership, import rules forbidding web↔agent, boundary tests; migrations
   split into their own component; per-component database roles (ADR 0025 steps 1–4).
4. ADR 0021 step 1 — the four schemas.
5. Frontend validation and hardening pass (section 11), including the **two-question pricing setup**
   (ADR 0033) — the highest-value UX change in the plan and the one gating catalog completion.
6. Append-only audit log and the retention schedule (ADR 0030).
7. Learning-loop **capture** only — record owner replies after handoff with their full context, scoped
   per tenant, changing no behaviour (ADR 0031).

**Phase 1 — prove the channel (gated on a Meta test number)**
7. Record real payloads; contract test every event type, including `account_offboarded` and
   `account_reconnected`; replace the synthetic fixtures.
8. Verify Embedded Signup v4 and coexistence end to end; pin `graph_version`.
9. Quality-rating and number-status telemetry into the Health board; get the `operator_alert` template approved.

**Phase 2 — prove the agent (gated on an LLM API key)**
10. Expand evals to ~80 scenarios with adversarial and sales-effectiveness dimensions.
11. Run them against candidate models; choose on safety, then selling quality, then latency, then cost;
    record the choice and the measured cost per conversation in an ADR.
12. Re-measure NFR-2 with a real model; set plan inclusions from real numbers.

**Phase 3 — platform plane**
13. Admin plane on its own origin with MFA and break-glass (ADR 0028).
14. Founder dashboard, four boards (ADR 0028).
15. Monitoring and email alerting with grouping and cooldowns (ADR 0029).

**Phase 4 — pilot readiness**
16. Five pilot shops; every conversation reviewed in week 1 (the retention mechanism, not a QA step).
17. DPDP pack: DPA, retention notice, breach runbook, legal placeholders filled.
18. Load test at NFR-5 with a real model; distributed trace ids across queue hops.
19. Learning-loop **proposals** — scope classifier, the WhatsApp question flow and the owner's view of
    what has been learned; per-tenant, off until the owner opts in, with eval scenarios for a learned
    fact that is wrong, stale or contradicts configuration (ADR 0031).

**Definition of done per phase:** acceptance checks pass, invariants still tested, seams intact, ADRs
written, PROGRESS.md updated, new configuration in `.env.example`, no unexplained TODOs.

## 14. Top risks

| Risk | Why it is the top one | Mitigation |
|---|---|---|
| Meta Tech Provider approval slow or refused | Gates self-serve onboarding, which is the entire distribution model for many small shops | Start this week; pilot on the platform's own WABA; keep compliance pages and positioning strictly CR-1 |
| Meta reprices or retightens the AI clause | Happened twice in 2026 already; could remove the margin or the permission | Channel adapter keeps other channels possible; per-tenant cost visible from day one |
| Variable cost above what shops will pay | ₹2.6 per conversation against ₹999 tiers leaves little room | Caching first; measure before pricing; inclusions in the low hundreds |
| Evals pass while selling is weak | Suite proves safety, not persuasion — a safely useless assistant would ship green | Sales-effectiveness eval dimension before pilot |
| Support load at volume | Non-technical owners at scale, with a solo founder | WhatsApp-first setup, self-healing onboarding, deflection, support metrics on the Growth board |
