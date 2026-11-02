# 0029 Platform alerting: email to the founder, never to subscribers

**Status:** accepted · 2026-11-02 · extends ADR 0017 (alert routing) with email and a severity model

## Context

ADR 0017 routes operator alerts to a webhook and optionally to WhatsApp numbers. The founder wants
something more direct: **email, to the founder, whenever the app is doing something bad** — and explicitly
not to the business owners who subscribe.

That separation already exists in principle: owner-facing events (handoffs, knowledge gaps, daily summary,
billing) go to the shop over WhatsApp and in-app; platform-facing events (dead letters, disconnections,
quality drops) go to the operator. What is missing is email, a severity model, and protection against the
failure mode that kills every alerting system: a solo founder who gets two hundred emails and stops
reading them.

## Options

1. **Email every event.** Simple, and unreadable within a week.
2. **Severity routing with grouping, deduplication and cooldowns.** More to build; alerts stay meaningful.
3. **Rely on an external monitoring vendor.** Another account, another bill, another thing to secure, for
   one person at pilot scale.

## Decision

Option 2.

**Delivery is behind a port with SMTP as the baseline.** Any provider that speaks SMTP works on day one
with no lock-in, which satisfies the swappable-vendor requirement (NFR-12) and keeps the choice of a
transactional provider reversible. Move to a provider API only if deliverability demands it.

**Four severities, as the founder specified — Critical, High, Medium, Low.** Severity decides the channel:

| Severity | Means | Examples | Delivery |
|---|---|---|---|
| **Critical** | money, data or customers are being harmed now | an invariant violation (a price that did not come from the pricing engine is always Critical), inbound messages lost, a tenant-isolation failure, ingress down for every shop | immediate email plus WhatsApp and the webhook; repeats every 30 minutes until resolved |
| **High** | one shop or one dependency is broken | a tenant disconnected from Meta (FR-ON-4), Meta quality drop, payment gateway failing, every LLM provider failing at once, dead letters accumulating | immediate email; 30-minute cooldown per cause |
| **Medium** | degrading, not yet broken | rising send failures, LLM fallback or error rate climbing, queue age past threshold, cost spike per tenant or platform-wide | hourly rollup email |
| **Low** | worth knowing | eval score drift, slow queries, retention job results, trial expiries | one daily digest |

The line between Critical and High is "is harm happening right now to more than one place, or to money". A
Critical alert that turns out not to meet that bar is a bug in the rule, not a judgement call.

**Alert fatigue is treated as a correctness requirement.** Alerts are grouped by rule, tenant and cause,
deduplicated with a per-rule cooldown, and carry an occurrence count rather than arriving one per event.
A recovery notice closes the loop so an inbox is never left implying something is still broken. Every
alert names the tenant, the rule, what it means in plain words, and the one place to go and look.

**Alerts never reach subscribers.** The sender address, the templates and the routing are platform-only.
Subscriber-facing messaging stays exactly where it is. This is enforced by a test, not by convention:
nothing in the platform alert path may resolve a business owner's address or number.

**Recipients are configuration** (`ALERT_EMAIL_TO`), so a second person can be added without a deploy.

## Consequences

- The founder learns about a broken tenant from an email rather than from the tenant, which is the
  difference between a save and a churn.
- Grouping means an alert can be a few minutes late. Critical is never held back by grouping; correctness beats
  instantaneity everywhere else.
- Email adds a vendor and a deliverability concern. SMTP behind a port keeps that cheap and swappable, and
  the webhook route from ADR 0017 remains as a second path if email fails.
- Every new alert rule needs a severity and a cooldown at the point it is written, or it does not ship.

## How to revisit

If Critical volume ever exceeds a handful a week, the problem is the system, not the alerting — fix the cause
rather than raising the threshold. If the platform team grows past two people, route by ownership and
revisit whether an external incident tool earns its cost.
