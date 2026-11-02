# 0032 Sell plans by number of conversations

**Status:** accepted · 2026-11-02 · founder's direction, confirmed against what the market does

## Context

Plan prices and inclusions have been placeholders since ADR 0019. The founder's direction: package by
number of conversations, so a shop that grows upgrades, rather than selling unlimited use that nobody
ever outgrows. The founder also ruled out billing by AI usage, and asked what comparable products do.

**What the Indian WhatsApp tools do** (secondary sources, Nov 2026, to be confirmed on each vendor's own
page): AiSensy from about ₹1,500/month, Interakt about ₹1,166/month billed quarterly, WATI ₹2,999/month,
Gallabox ₹2,999/month. They meter **seats and broadcasts**, not conversations, and pass Meta's
per-message fee through with a markup.

**What the AI support products do:** Intercom Fin charges about $0.99 per resolution, Zendesk roughly
$1–2, Salesforce Agentforce launched at $2.00 per conversation. The whole category is moving from seats
to outcomes.

So nobody in the Indian WhatsApp segment prices by conversation yet, while every serious AI product
prices by conversation or resolution. The founder's instinct lands between the two and is differentiated.

## Options

1. **Seats**, like the Indian incumbents. Meaningless to a one-person shop, which is most of the market.
2. **Conversations included per month, as a package limit.** Matches the value delivered and our cost shape.
3. **Per resolution**, like Fin. Best value alignment, but "resolution" is a definition argument with a
   non-technical owner every month, and disputes land on a founder with no support team.
4. **AI usage (tokens).** Ruled out by the founder, and correctly: an owner cannot predict it, and it
   charges them more when the assistant works harder.

## Decision

Option 2, **as a package limit and not as per-unit billing** (founder: "we are not charging per things
we give in packages and just restrict the counts per package"). The shop buys a package; the count is
capped; reaching the cap prompts an upgrade. **No overage charge.** The metering ADR 0019 built is reused;
its overage rate is left unused rather than removed.

Why a cap beats billing the excess: a non-technical owner cannot be sent a bill they did not expect and
did not choose. Bill shock is one of the fastest ways to lose a small shop, and it arrives exactly when
the shop is doing well. A cap turns the same moment into an upgrade conversation, which is the behaviour
the founder wanted the packages to produce.

**Why conversations is the right unit here:** it is the unit the owner already thinks in — "how many
customers messaged me" — it rises with the value they get, and it makes upgrading a natural consequence
of their business growing. Seats are irrelevant to a shop where the owner is the only user.

**Inclusions follow measured cost**, not instinct. At roughly ₹2.6 variable cost per conversation
(ADR 0026, ADR 0027) and a target of variable cost near 35–40% of plan price, the shape is:

| Tier | Price/month | Conversation cap | ≈ per day |
|---|---|---|---|
| Entry | ~₹999 | ~150 | ~5 |
| Growth | ~₹2,499 | ~400 | ~13 |
| Pro | ~₹5,999 | ~1,000 | ~33 |

These are provisional and get set from Phase 2 measurement before anything is published. A ~₹999 entry
undercuts every incumbent listed above, which matters for a land-and-expand motion.

**Meta's free tier reinforces it.** 1,000 free service messages per shop number per month means the
smallest shops cost almost nothing to serve, so the entry tier is genuinely profitable rather than a
loss leader.

**A conversation is defined once, in plain words, and shown in the app**: a customer the assistant
replied to within a calendar month, counted once however many messages pass. Owners must be able to
check the count themselves and see it rise, or the meter becomes a support burden.

## Consequences

- Pricing is defensible from unit cost instead of guessed, and the upgrade path is built into the
  product rather than relying on a sales conversation.
- Inclusions in the low hundreds will look small beside competitors advertising unlimited broadcasts.
  The answer is that those are broadcast credits, not answered leads; marketing has to make that clear
  or the entry tier reads as stingy.
- Hitting the cap must never silently stop answering customers. The sequence is: warn on WhatsApp and
  in-app well before the limit, then at the limit keep answering for a short grace period while asking
  the owner to upgrade. A shop whose assistant goes quiet mid-month loses real sales and blames us.
- Revenue per shop is capped by the tier, so upgrades matter more than overage ever would. The warning
  copy and timing are effectively the upgrade funnel.
- Each tier's margin depends on ADR 0026 and ADR 0027 landing first; without channel and model cost
  accounting these numbers cannot be trusted.

## How to revisit

Set the real numbers from Phase 2 measurement in a superseding ADR. If owners dispute counts or
conversations prove a poor proxy for value, per-resolution is the next model to test — but only with
support capacity to handle the definition arguments it brings.
