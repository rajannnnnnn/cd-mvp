# Product Requirements Document (PRD)

## Problem, vision and summary

Small Indian businesses lose sales on WhatsApp because nobody answers fast enough, prices are quoted inconsistently, and interested customers go cold. The owner is usually serving walk-in customers while WhatsApp messages pile up.

**Product:** an AI sales assistant that replies on the business's own WhatsApp number, answers from owner-configured facts and catalog, quotes and negotiates within owner-set pricing rules, moves leads toward a commitment (an order or a store visit), and hands off to the owner when it should. It writes in the owner's voice and paces replies like a person.

**Vision:** every small business gets a tireless, honest salesperson on WhatsApp that knows its products, never quotes an unauthorized price, and learns how that business sells.

**Business model:** monthly subscription per business, tiered by conversation volume and features. Meta's per-message fees for any templates are billed by Meta directly to the business's own WhatsApp Business Account.

## Glossary

| Term | Meaning |
|---|---|
| Tenant / business | One subscribing business; all its data is isolated from other businesses |
| Owner | The business owner or staff who configures the business and receives handoffs |
| Customer | A person messaging the business on WhatsApp |
| Turn | One AI decision cycle in response to one or more batched customer messages |
| Handoff | Routing a conversation to the owner because the AI should not or cannot proceed |
| Commitment / deal | A captured order or store-visit agreement awaiting owner confirmation |
| Floor price | The lowest price the owner allows for an item; never revealed to the LLM or customer |
| Cloud API | Meta's hosted WhatsApp Business Platform API the product sends and receives through |
| Coexistence | One number active on both the WhatsApp Business app and the Cloud API |

## Users and personas

The primary user is a small business owner who already sells on the WhatsApp Business app; the product must work even when that owner has low literacy.

| Persona | Profile | Needs | Design implication |
|---|---|---|---|
| Shop owner (primary) | Local retail or service business, 1–10 staff, busy in-store, uses WhatsApp daily | More sales from WhatsApp, no wrong prices quoted, control when needed | Voice and WhatsApp-based setup, daily summary on WhatsApp, simple stop/start |
| Low-literacy owner | Comfortable with WhatsApp voice notes and UPI, not with forms or dashboards | Same as above, without reading or typing | Assisted onboarding, voice setup, voice summaries, no required web use |
| Staff member | Helps the owner answer customers | See handoffs, reply from the Business app | Handoff alerts can go to more than one person |
| Customer (end user) | Messages the shop in English, Hindi or Hinglish, often in several short messages or voice notes | Fast, accurate, natural answers; a fair price; a human when needed | Turn batching, language mirroring, honest answers, easy escalation |
| Platform operator (you) | Runs the SaaS | Onboard businesses, monitor health and quality, support | Admin console, per-tenant health, Meta quality alerts |

## Scope

v1 sells through inbound conversations only: the AI never starts a conversation with a customer. Everything is built multi-tenant and behind interfaces, so v2 items plug in without restructuring.

| Capability | v1 (prototype + pilot) | v2 | Out of scope |
|---|---|---|---|
| Onboarding | Operator-assisted; Embedded Signup with coexistence once Tech Provider approval lands | Self-serve signup | Owners configuring webhooks |
| Business setup | Admin form and JSON import; owner voice/text setup interview | Catalog import from spreadsheets and Shopify-like sources | — |
| Catalog and pricing | Products, variants, five disclosure modes, floors, concession steps, offers | Bulk pricing rules, customer-segment pricing | Dynamic market-based pricing |
| Conversation | Inbound replies, turn batching, interrupts, language mirroring, owner voice examples | Image understanding (product photos sent by customers) | General-purpose chat unrelated to the business |
| Voice | Customer voice notes transcribed | Voice replies to customers | Phone calls |
| Sales | Lead stages, qualification, quoting, negotiation within rules, commitment capture | Payment links, order tracking | In-chat payment processing by the platform |
| Handoff | Alerts to owner, AI pause on owner reply, stop/start commands | Staff routing and assignment | — |
| Outbound | None | Template follow-ups with opt-in, reminders | Bulk marketing broadcasts |
| Owner reporting | Daily summary on WhatsApp; minimal web dashboard | Full pipeline analytics | — |
| Learning | Signals captured (timings, outcomes) | Learned end-of-turn, timing and style models; bandit tuning | — |
| Billing | Manual for pilots | Razorpay or Cashfree subscriptions with UPI Autopay | — |

## Functional requirements

Requirement IDs are stable so code, tests and the build prompt can reference them. All are v1 unless marked v2.

### Onboarding and connection

| ID | Requirement |
|---|---|
| FR-ON-1 | An operator can create a business, its owner users and their alert numbers. |
| FR-ON-2 | The owner connects WhatsApp through Meta Embedded Signup with the coexistence option; the backend exchanges the returned code for a token, stores it encrypted, and subscribes the app to the owner's WhatsApp Business Account. |
| FR-ON-3 | Before Tech Provider approval, numbers can be registered manually under the platform's own WhatsApp Business Account for pilots. |
| FR-ON-4 | The system detects disconnection (coexistence inactivity, revoked access, token failure), stops the AI for that business and alerts the owner and the operator. |

### Business configuration

| ID | Requirement |
|---|---|
| FR-CF-1 | Owners maintain a business profile: name, address, hours, delivery areas, payment modes, return and other policies, free-form facts. |
| FR-CF-2 | Owners maintain a catalog of products with variants; every price lives on a variant. |
| FR-CF-3 | Each variant has a pricing policy with a disclosure mode: fixed, range, starts_from, after_qualifying, on_request. |
| FR-CF-4 | Each variant can define negotiability, a private floor price, concession steps, concession conditions (quantity, advance payment, repeat customer), and whether the AI may negotiate it. |
| FR-CF-5 | Owners define offers (percent, flat, bundle, free item) with conditions and start and end dates. |
| FR-CF-6 | Owners set sales style: proactiveness, whether offers may be mentioned unprompted, honorifics, handoff triggers (for example order value above a threshold). |
| FR-CF-7 | Owners provide example replies in their own voice, by situation. |
| FR-CF-8 | Owners can configure by voice or text over WhatsApp; the system extracts structured changes, reads them back, and applies them only after confirmation. |
| FR-CF-9 | Owners mark personal contacts the AI must never reply to. |

### Conversation handling

| ID | Requirement |
|---|---|
| FR-CV-1 | Every inbound webhook is persisted before acknowledgment and processed asynchronously; duplicates by Meta message ID are ignored. |
| FR-CV-2 | Consecutive customer messages are batched into one turn using an end-of-turn decision, bounded by a maximum wait. |
| FR-CV-3 | A new customer message during waiting, composing or typing cancels pending output and replans with the merged context. |
| FR-CV-4 | Customer voice notes are transcribed and treated as text. |
| FR-CV-5 | Replies mirror the customer's language, script and formality, and follow the owner's voice examples. |
| FR-CV-6 | The AI may reply in several messages, react with an emoji, send a holding message, or stay silent when no reply is needed. |
| FR-CV-7 | Read receipts, typing indicators and inter-message delays are scheduled, not instantaneous. |
| FR-CV-8 | Free-form messages are never sent outside Meta's 24-hour customer service window. |
| FR-CV-9 | When the owner replies manually from the Business app, the AI pauses in that conversation for a configurable period. |
| FR-CV-10 | The owner can send stop or start commands to pause or resume the AI for the whole business. |
| FR-CV-11 | Out-of-scope requests (unrelated to the business) are politely declined and steered back. |

### Sales

| ID | Requirement |
|---|---|
| FR-SL-1 | Each conversation tracks a lead stage: new, exploring, interested, negotiating, ready_to_buy, won, lost. |
| FR-SL-2 | The AI captures qualification data: needs, quantity, budget signals, timeline, delivery location. |
| FR-SL-3 | Any price stated to a customer must come from the pricing engine; the LLM never sees floor prices. |
| FR-SL-4 | Negotiation proceeds in concession steps, may require a concession from the customer, never goes below floor, and hands off when the customer insists below floor. |
| FR-SL-5 | on_request items never get a price from the AI; requirements are collected and handed off. |
| FR-SL-6 | Urgency or scarcity is mentioned only when backed by real stock or a real offer end date. |
| FR-SL-7 | The AI captures commitments (order details or a visit time) and alerts the owner, who marks deals won or lost. |
| FR-SL-8 | After a customer goes quiet, at most one in-window nudge; "not interested" ends selling in that conversation. |

### Handoff and owner reporting

| ID | Requirement |
|---|---|
| FR-HO-1 | Handoffs are created with a reason (unknown answer, on-request price, below floor, customer asked for a human, complaint, high value) and alerted to the owner on WhatsApp. |
| FR-HO-2 | Questions the AI could not answer are logged as knowledge gaps; owner answers become business facts after confirmation. |
| FR-RP-1 | A daily summary goes to the owner: conversations, new customers, buying interest, commitments, complaints, open handoffs, knowledge gaps. Text by default, voice when configured. |
| FR-RP-2 | A web dashboard shows the same metrics, the conversation list with filters, lead pipeline and knowledge gaps. |

### Platform operations

| ID | Requirement |
|---|---|
| FR-OP-1 | The operator console lists tenants with connection status, Meta quality rating, AI status, volume and error rates. |
| FR-OP-2 | Every AI turn is recorded with its decision, model, token usage and latency, for audit and learning. |
| FR-OP-3 | Raw webhook payloads are logged and replayable. |

## Non-functional requirements

Targets are for v1 pilot scale and must not require redesign to reach the scale-out targets.

| ID | Area | Requirement |
|---|---|---|
| NFR-1 | Webhook latency | Acknowledge Meta webhooks with HTTP 200 in under 300 ms at p99; no LLM or network work in the request path. |
| NFR-2 | Reply latency | Processing time from end-of-turn decision to first outbound message under 8 s at p95, excluding intentional human-like delays. |
| NFR-3 | Durability | No inbound message lost: persisted before acknowledgment; all processing retryable and idempotent. |
| NFR-4 | Ordering | Per-conversation processing is serialized; different conversations process in parallel. |
| NFR-5 | Scale (v1) | 50 businesses, 5,000 conversations per day on one machine. |
| NFR-6 | Scale (target) | 10,000 businesses, 1M conversations per day by adding instances, partitioning queues and moving components to separate machines, with no data model change. |
| NFR-7 | Isolation | Tenant isolation enforced by the database (row-level security and composite foreign keys), not only by application code. |
| NFR-8 | Availability | 99.5% monthly for message processing in v1; graceful degradation when the LLM provider fails (holding message plus owner handoff). |
| NFR-9 | Security | Access tokens encrypted at rest; secrets only from environment or a secret store; webhook signatures verified. |
| NFR-10 | Privacy | Data used only to serve the owning business; per-tenant export and deletion; compliant with India's DPDP Act. |
| NFR-11 | Portability | Frontend and backend deploy independently; the whole system runs on one machine or split across many with configuration only. |
| NFR-12 | Changeability | Components communicate through defined interfaces and queue messages; LLM provider, transcription provider, queue backend and messaging channel are swappable adapters. |
| NFR-13 | Observability | Structured logs, metrics and traces carry tenant ID, conversation ID and turn ID. |
| NFR-14 | Cost visibility | LLM and transcription cost tracked per turn and per tenant. |

## Compliance and honesty requirements

The product is a customer-service and sales tool for each business, never a general-purpose AI assistant; this positioning is required by Meta's WhatsApp Business Solution Terms effective January 15, 2026.

| ID | Requirement | Source |
|---|---|---|
| CR-1 | The AI answers only about the business, its products and the customer's purchase; unrelated requests are declined. Marketing, app review material and UI never present the product as "chat with AI". | Meta WhatsApp Business Solution Terms (AI providers clause) |
| CR-2 | Free-form messages only within 24 hours of the customer's last message; anything later uses an approved template. | WhatsApp Cloud API rules |
| CR-3 | No business-initiated messages to customers without prior opt-in; opt-out requests are recorded and honored. | WhatsApp Business Messaging Policy |
| CR-4 | A human escalation path is always available and offered when the customer asks. | WhatsApp Business Messaging Policy |
| CR-5 | Businesses in categories prohibited by Meta's Commerce Policy cannot be onboarded. | WhatsApp Commerce Policy |
| CR-6 | No false urgency, false scarcity or invented claims. | CCPA Guidelines for Prevention and Regulation of Dark Patterns, 2023 |
| CR-7 | The AI never claims to be human when sincerely asked; it identifies as the shop's assistant and offers the owner. Human-like pacing and tone are allowed. | Product policy |
| CR-8 | Sensitive data (full card numbers, passwords, OTPs) is never requested in chat. | Product policy |
| CR-9 | Privacy policy, terms of service and a data deletion process are published on the product domain. | Meta app review; DPDP Act |
| CR-10 | Customer data of one business is never used to serve another business. | Product policy; DPDP Act |
| CR-11 | The annual Meta Data Use Checkup is completed on time. | Meta Platform Terms |

Before launch, read the current WhatsApp Business Messaging Policy and Commerce Policy on Meta's site; the summaries above came from secondary sources.

## Success metrics

The pilot succeeds when the AI quotes zero unauthorized prices and four of five pilot owners keep using it after 30 days.

| Metric | Definition | Pilot target |
|---|---|---|
| Unauthorized prices | Prices stated that did not come from the pricing engine, found by reviewing every pilot conversation in week 1, then by sampling | 0 |
| Wrong facts | Statements contradicting the business profile or catalog | 0 in reviewed samples |
| Autonomous handling | Conversations resolved or committed without handoff | ≥ 60% by week 4 |
| Lead-to-commitment | Conversations reaching an order or visit commitment, out of those showing buying interest | Baseline in week 1, improving by week 4 |
| Owner-confirmed sales | Commitments the owner marks won | Tracked; baseline only |
| Reply latency | Processing time, p95 | < 8 s |
| Quality rating | Meta quality rating of each pilot number | No drop |
| Bot suspicion | Share of conversations where the customer asks whether it is a bot | Tracked; lower over time |
| Retention | Pilot owners still active after 30 days and willing to pay | ≥ 4 of 5 |
| Unit cost | LLM + transcription + template cost per conversation | Measured to set pricing |

## Risks and open decisions

The largest risk is Meta access: business verification and Tech Provider review gate onboarding real businesses and are outside the team's control.

| Risk | Impact | Mitigation |
|---|---|---|
| Meta verification or app review delayed or rejected | No self-serve onboarding | Start week 0; pilot on the platform's own account; keep compliance pages ready |
| Meta policy change on AI use | Product blocked | Strict business scoping (CR-1); monitor Meta terms; channel adapter allows other channels |
| AI states wrong price or fact | Owner trust lost, legal exposure | Pricing engine owns all numbers; facts-only answering; week-1 full review |
| Customers feel deceived | Blocks, reports, quality drop | CR-7 honesty rule; quality monitoring |
| Unit cost above what shops pay | Unprofitable | Model cascade; measure per-turn cost in pilot before pricing |
| Coexistence disconnects silently | Customers unanswered | Disconnect detection and alerts (FR-ON-4) |
| Personal chats flow into the system | Embarrassment, privacy breach | Personal contact list, history sync off by default, recommend a dedicated SIM |
| Owners cannot configure complex pricing | Product unused | Voice setup with read-back, business-type templates, assisted onboarding |

### Open decisions

- [ ] Legal entity that owns the product and holds the Meta account.
- [ ] Pilot business categories and the five pilot shops.
- [ ] Default AI negotiation setting per item (recommended: off, owner opts in).
- [ ] LLM and transcription vendors, chosen by testing on real Hinglish conversations.
- [ ] Subscription price, set after pilot cost measurement.
