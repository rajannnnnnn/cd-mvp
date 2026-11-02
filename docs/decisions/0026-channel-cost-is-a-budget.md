# 0026 Channel cost is a budget, not an afterthought

**Status:** accepted · 2026-11-02

## Context

Meta changed how our traffic is billed, twice, and the product was designed before both changes:

- Since **1 July 2025**, billing is per delivered message, not per conversation.
- Since **1 October 2026**, **service messages are billed** — 1,000 free per business phone number per
  calendar month, then the recipient market's utility rate (India ≈ ₹0.115). Our replies are service
  messages: free-form text inside the 24-hour window.

(Both figures are from secondary sources; Meta's own domains are blocked from this environment and they
must be re-read against Meta's pricing page before pilot.)

Two consequences the code does not know about. First, **nothing counts Meta messages** — a search for
per-message cost accounting finds nothing. Second, human-like delivery (ADR 0014) splits a reply into
several messages, and **each part is now a separate billed message**. A design choice made for warmth
became a cost multiplier a month ago.

Owner alerts compound it: handoff alerts and the daily summary go to the owner's WhatsApp, usually
outside the window, so they are utility template messages — billed, and requiring Meta approval.

## Options

1. **Ignore it until the Meta bill arrives.** Unacceptable: pricing decisions are being made now, and
   gross margin per tenant is unknown without this.
2. **Cut multi-part replies.** Saves money immediately and makes the assistant read like a form letter.
   The founder's priority order puts answer quality above cost.
3. **Make channel cost a measured, budgeted quantity**, with the part count a tunable per-tenant budget
   rather than an accident.

## Decision

Option 3.

**Count every message.** Each outbound send records its category (service, utility, marketing), its
tenant and its number. A per-number monthly counter models the 1,000-message free tier so the marginal
cost of the next message is known at all times, not reconstructed from an invoice. Meta's own billing
stays the source of truth; ours is the estimate we steer by, and the two are reconciled.

**Reply parts become a budget.** `MAX_REPLY_PARTS` is per-tenant configuration with a sane default, not a
constant. The delivery planner already produces one action per part, so this is a cap at planning time,
and the writer is told the budget so it composes to fit rather than being truncated.

**Reduce messages before reducing quality.** When a tenant is near or past its free tier the lever is
fewer, better-composed parts — never a weaker model. Model cost is addressed separately in ADR 0027 by
caching, which costs no quality at all.

**Owner alerts are batched.** Handoff alerts group with a per-hour cap; the daily summary is one message.
An owner who gets twelve separate alerts in an hour is both expensive and badly served.

**Cost surfaces per tenant.** Model cost and channel cost, separately, on the founder dashboard's Money
board (ADR 0028), with gross margin per tenant. Channel cost per conversation is the number plan pricing
must be set from.

## Consequences

- Plan pricing can finally be set from measured variable cost rather than guesses. Early arithmetic puts
  it near ₹2.6 per conversation all-in, which implies included-conversation counts in the low hundreds —
  a material change to the placeholder plans.
- The free tier means small shops cost almost nothing to serve, so a cheap entry tier is genuinely
  profitable. That shapes the pricing ladder in our favour.
- A part budget slightly flattens the assistant's rhythm at the margin. Whether to spend money on
  naturalness is a product call, so the cap's default is a founder decision; the mechanism is built
  either way.
- Adds a counter on the hot send path. It is one row per send next to work already being done.

## How to revisit

If Meta changes the free tier or the rates — likely, given 2026 — the free-tier model is configuration,
not code. If measurement shows channel cost is a small fraction of model cost, relax the part budget and
spend the saving on naturalness; the counters will say so.
