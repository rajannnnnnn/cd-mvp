# 0028 Admin plane: own origin, own role, aggregates by default

**Status:** accepted · 2026-11-02

## Context

The founder needs to see how the SaaS is doing: growth, money, health, and whether the assistant is
actually selling. The existing operator console (FR-OP-1) answers per-tenant support questions; it is not
a business dashboard and it shares an origin and an API with the tenant-facing app.

The founder's instruction was "never compromise on security", and asked whether this belongs on a
subdomain. It carries the most dangerous access in the product: every tenant, every number, every
conversation.

## Options

1. **Extend the existing operator console in the tenant app.** Cheapest; one XSS in the owner app reaches
   platform-wide admin. Rejected.
2. **Own origin, own API role, aggregates by default.** More to build; a compromise of the public app
   cannot reach it.
3. **Separate service and database.** Strongest, but duplicates the data layer for one user at pilot
   scale, and reconciling two stores is its own class of bug.

## Decision

Option 2.

**Isolation.** The founder console is served from its own origin (`admin.<domain>`) by its own backend
role, not routed from the public API. Strict CSP, no third-party scripts, its own session cookie scope.
It gets its own database role with the narrowest grants that serve it — read-mostly, aggregate-first.

**Authentication is stronger than the tenant app's.** The product signs owners in with one-time codes over
WhatsApp, which is right for them and not enough here: a second factor (passkey preferred, TOTP accepted)
is mandatory, sessions are short, and `OTP_ACCEPT_ANY` can never apply. An IP allowlist is supported and
off by default.

**Platform staff cannot read customer conversations by default.** The boards show aggregates. Reading a
real conversation is **break-glass**: it requires a stated reason, is time-boxed, is written to the
append-only audit log, and is visible in the shop's own audit view. A product that sits inside people's
private chats should not let its operator browse them casually, and under DPDP we are the shops' processor,
not an owner of their customers' data.

**Four boards**, because "how is the business doing" is four different questions:

| Board | Leads with |
|---|---|
| **Sales outcome** | autonomous handling %, lead→commitment rate, owner-confirmed wins, bot-suspicion rate, wrong-fact and unauthorized-price counts (must read 0) |
| **Growth** | activation funnel — signed up → number connected → first AI reply → first commitment → paid; trial→paid; churn; support load |
| **Money** | MRR, ARPU, variable cost per tenant split model vs channel, gross margin per tenant, overage |
| **Health** | webhook p99, queue depth and oldest-job age, dead letters, LLM error and fallback rate, send failures, eval pass rate, Meta quality rating and number status per tenant |

Sales outcome leads because the product's goal is more sales; a dashboard that opens on MRR would hide the
thing that causes MRR.

**Monitoring and auditing live on this same origin.** A third origin would mean a third auth surface to
secure for no isolation gain: the data is equally sensitive and the audience is one person.

**It is a separate frontend deployable**, built from the same component library as the owner app, so the
tenant app's bundle never contains admin code.

## Consequences

- A compromise of the public app or the marketing site cannot reach platform-wide data.
- Aggregate-first means some support questions need a break-glass step. That friction is deliberate and
  the audit trail is worth it; if it proves too slow in practice, widen the aggregates, not the access.
- New surface to build and test: its own browser specs, its own auth tests, its own boundary rules.
- Per-tenant gross margin requires ADR 0026's channel accounting and ADR 0027's model cost records. The
  Money board is only as good as those.

## How to revisit

If a second person joins the platform team, add roles and per-role scoping before widening any default
access. If aggregates genuinely cannot answer support questions, add purpose-built aggregates rather than
turning break-glass into routine.
