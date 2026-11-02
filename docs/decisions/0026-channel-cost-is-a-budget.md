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

**Splitting becomes adaptive, not a rule** (founder's instruction, and it corrects an earlier
underestimate of the cost). Today pacing can split habitually. Instead:

- **One message is the default.** A reply is split only when the content is genuinely long.
- **Mirror the customer.** If they write one long message, answer in one; if they write in short bursts,
  match that. This extends FR-CV-5 (mirror language, script and formality) from wording to message shape,
  which is where it always belonged.
- `MAX_REPLY_PARTS` stays as a per-tenant ceiling, a safety valve rather than a target.

**Correcting the arithmetic.** An earlier estimate put the whole lever at ~₹150/month by comparing a 1.8
average against 1.3. Against a habit of three parts it is much larger. For 900 conversations and three AI
replies each (2,700 replies), after the 1,000 free messages:

| Average parts per reply | Billed messages | Meta cost/month |
|---|---|---|
| 1 | 1,700 | ≈ ₹196 |
| 2 | 4,400 | ≈ ₹506 |
| 3 | 7,100 | ≈ ₹817 |

So the spread is roughly **₹620 a month** on one busy shop — about a quarter of a ₹2,499 plan, not a
rounding error. Splitting had to become content-driven on cost grounds as well as UX grounds.

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
- Adaptive splitting improves both sides at once: short answers stop arriving as three buzzes, and the
  bill falls. There is no quality-versus-cost trade here, which is why it was the wrong place to look
  for a trade-off in the first place.
- Adds a counter on the hot send path. It is one row per send next to work already being done.

## How to revisit

If Meta changes the free tier or the rates — likely, given 2026 — the free-tier model is configuration,
not code. If measurement shows channel cost is a small fraction of model cost, relax the part budget and
spend the saving on naturalness; the counters will say so.

## Implementation note (2026-11-02)

Counting is built (migration 0014, `modules/billing/meta_usage.py`, operator endpoint
`GET /api/v1/operator/businesses/{id}/meta-usage?month=YYYY-MM`). It reads what is already recorded rather than adding
a write to every send: customer messages that reached the customer, and owner alerts that reached the owner. Two facts
were added to make the estimate correct: `messages.sent_via` (`api` or `business_app`) — an owner reply typed in the
WhatsApp Business app comes back as an echo but Meta does not bill it — and `owner_messages.whatsapp_number_id`, since
owner alerts go out from the shop's own number (using its free allowance) or, when that is down, from the platform's
sender. **Who pays** follows whose WhatsApp account the number is on: a number holding its own token came through
Embedded Signup and Meta bills the shop; a pilot number under our account bills us. That corrects the earlier framing
in this ADR: once shops connect their own numbers, customer-reply fees are the shop's cost, not the platform's — they
still matter to the shop's total cost of using the product. Adaptive reply splitting is a separate agent item.
