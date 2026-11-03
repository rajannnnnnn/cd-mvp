# 0031 Per-tenant learning loop: propose, never assume

**Status:** accepted · 2026-11-02 · founder request, with scope limits the founder set in the same breath

> **Update 2026-11-03:** there is no daily summary ([ADR 0040](0040-daily-summary-removed.md)), so proposals are not carried by one: they are asked on their own, at most a few a day, and the answer rate is measured the same way.

## Context

When the AI hands a conversation to the owner and the owner answers it, the answer is knowledge the AI
did not have. The founder wants that captured so the assistant gets better at each shop over time, and
named the trap in the same sentence: an owner replying "no refund" may mean no refunds ever, or no
refunds on that product, or not for that customer, or not after something agreed offline. Learning the
general rule from the specific reply would make the assistant confidently wrong, which is worse than
not learning at all.

Two things already in the product are the right rails for this, and both are built and tested:
FR-HO-2 knowledge gaps (an unanswered question becomes a business fact **after owner confirmation**) and
FR-CF-8 config-by-chat (structured changes **read back** before they apply). This decision extends those
rather than inventing a second mechanism.

It also sits inside Meta's AI Providers clause, which forbids using WhatsApp data to train or improve
models but excepts work **exclusive to that one business**. Per-tenant learning is the one shape allowed.

## Options

1. **Learn silently from owner replies.** Fastest to build, and exactly the failure the founder described.
2. **Capture, then propose the narrowest scope to the owner for confirmation.** Slower to pay off, but
   every learned fact has a human behind it.
3. **Don't learn; keep manual configuration only.** Safe and already shipped, but wastes the single
   richest signal the product has.

## Decision

Option 2, with hard scope limits.

**Only owner-authored text is a learning source.** Owner replies after a handoff, owner answers to
knowledge gaps, owner config-by-chat. **Never a customer's message** — a customer writing "the shop told
me returns are free" must never become a fact. That is prompt injection with a database behind it.

**What may be learned, and nothing else:** business facts and policies (the free-form profile fields),
answers to repeated unknown questions, and the owner's phrasing for the voice examples.

**What may never be learned:** prices, floors, concession steps, offers, negotiability, handoff triggers.
Money stays with the pricing engine and the owner's explicit configuration (INV-2, INV-3). This is what
keeps the loop from breaking anything that already works.

**Over-generalization is the thing being engineered against:**

- Every observation is stored with its full context — which customer, which product, which conversation,
  when — never as a bare sentence.
- Nothing is proposed from a single observation. The same answer must recur across **distinct
  conversations** before it is a candidate.
- Each candidate is classified by the narrowest scope the evidence supports: one order, one customer, one
  product, one category, a date range, or the whole shop. **The narrowest plausible scope is proposed**,
  not the broadest.
- A learned fact never overrides owner-set configuration, and conflicts are surfaced rather than merged.
- Everything learned is visible, attributable to the conversation it came from, and deletable in one tap.
  The owner can always see why the assistant says something.

**The owner is asked on WhatsApp, in plain words, with numbered choices** — never a settings screen, and
never jargon. For the founder's example:

> You told Ramesh there's no refund on the blue saree. Should I tell other customers the same?
> 1 — No refunds on anything
> 2 — No refunds on sarees
> 3 — Just that one order
> 4 — Don't save this

One digit answers it. Unanswered proposals expire and change nothing.

**Interruption budget.** Asking after every handoff would train owners to ignore us, so proposals ride the
daily summary by default, at most a small number per day, and only the highest-confidence candidate
interrupts outside it. Rate limits are per-tenant configuration, not constants.

**Nothing crosses tenants.** No pooled model, no shared statistics, no cross-shop prior — required by
Meta's clause and by CR-10.

**Build order.** Capture ships early, because recording signals is already required and changes no
behaviour. Proposing ships late, behind a per-tenant flag that is **off until the owner opts in**, after
the core pipeline is proven in pilot. This is the founder's "only if it is not breaking others".

## Consequences

- The assistant improves at a shop without anyone configuring anything, which is the point for owners who
  will never open the web app.
- Every learned fact is owner-confirmed, so a wrong one is the owner's own answer, recoverable in one tap,
  and auditable — not a silent model drift nobody can explain.
- Pays off slowly: a candidate needs repetition before it is proposed. That is the cost of not being
  confidently wrong.
- Adds a proposal store, a scope classifier and the WhatsApp question flow. The classifier is the
  genuinely hard part and should start conservative — when the scope is unclear, propose the narrowest
  one or do not ask at all.
- Learned facts enter the agent's context, so they are inside the evals' blast radius: the suite needs
  scenarios where a learned fact is wrong, stale or contradicts owner configuration.

## How to revisit

If owners ignore the proposals, the problem is the asking, not the learning — move everything into the
daily summary and measure the answer rate before adding any new mechanism. If owners answer but the
scopes come out wrong, tighten the classifier toward "ask only for shop-wide facts"; do not widen the
default scope. Per-tenant learned timing or end-of-turn models are a later enhancement on this same
per-tenant rule; they are models rather than facts, so they need their own ADR and their own eval gate.

## Implementation note (2026-11-02): recording step built

`learning_observations` (migration 0015), written by `capture_owner_answer` in the handoffs module — the learning is
about answers to handoffs, and both the conversation engine and the web API may use that module, which keeps the
web/agent separation of ADR 0025 intact. Recorded when an owner answers a conversation with an open handoff, from the
WhatsApp Business app (echo) or the dashboard: the owner's text (the only learning source), the customer's question
(context only), the handoff reason and the lead stage. Append-only for the tenant role; exported and deleted with the
shop (DPDP). Recording runs in its own savepoint, so a failure never undoes the owner's reply, and is counted
(`salesai_learning_capture_failed_total`). Nothing is learned and no behaviour changes yet.

"Never learn across shops" is enforced as a tested rule: row-level security per shop, and a test that fails if any
module other than `modules/handoffs/learning.py` and tenant export/delete references the table. It is labelled
**proposed INV-13** because invariants are the founder's to add to `TECHNICAL_DESIGN.md`.
