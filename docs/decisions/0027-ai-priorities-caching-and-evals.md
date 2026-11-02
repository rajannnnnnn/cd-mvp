# 0027 AI: security, then answer quality, then latency, then cost

**Status:** accepted · 2026-11-02

## Context

The founder set the order explicitly: **security → UX (answer quality first, then latency) → cost**. The
product's goal is more sales, so the assistant's job is to give real product detail and persuade toward a
commitment. A safe assistant that does not sell has failed.

Three findings from the current code:

- **Prompt caching is not implemented.** No `cache_control` anywhere in the agent. Every turn re-sends the
  system prompt, business profile, catalog slice and style examples at full input price.
- **The cheap-model slot points at `claude-haiku-4-5`** ($1/$5 per MTok). `claude-haiku-5-5` is $0.10/$0.50
  — ten times cheaper for the same slot.
- **The 32-scenario eval suite asserts invariants, not selling ability.** It can pass while the assistant
  is safely useless.

The adapter itself is already on current request shapes — no forced `tool_choice`, no sampling parameters,
`output_config` effort — so no migration is needed there.

## Options considered for cost

Model downgrade, shorter context, fewer pipeline calls, and caching. Only caching and the Haiku version
bump are free of quality risk; the rest trade answer quality for money, which the founder's order forbids
until the free levers are exhausted.

## Decision

**Security.** Floors never enter model context (INV-1) — structural, and it stays the primary defence.
Every reply passes send-time checks before leaving. Customer text is untrusted input: the evals gain
adversarial scenarios for floor extraction, instruction override, owner impersonation and tool-abuse
phrasing. Learned facts (ADR 0031) are owner-authored only, never customer-authored.

**Answer quality.** The writer runs on a strong model by default. The eval suite grows toward ~80
scenarios across Hindi, English and Hinglish and several business types, and gains a **sales-effectiveness
dimension** alongside the invariant graders: did it qualify the need, answer from real catalog fact, handle
the objection, advance the lead stage, ask for the commitment, avoid dead-ending. Prompt, model and
pipeline changes already gate on evals; that gate now measures both safety and selling.

**Latency.** NFR-2 allows 8s p95 to the first message, and human-like pacing already delays replies
deliberately by up to ~20s. The customer cannot perceive a slower model inside that envelope, so **pacing
buys latency headroom and we spend it on answer quality**, not on a weaker model. Thinking depth and effort
are tuned per pipeline stage with evals, not assumed.

**Cost, in this order, stopping when the target is met:**

1. **Prompt caching.** The per-tenant prefix — system prompt, business profile, catalog slice, style
   examples — is stable across turns and belongs behind a cache breakpoint, with volatile content
   (recent messages, timestamps) after it. Cache reads are $0.20/MTok against $2 input on Sonnet 5.5.
   Expected to remove roughly two-thirds of model cost per turn at zero quality cost. Verify with
   `cache_read_input_tokens`: if it reads zero across turns, something is silently invalidating the prefix.
2. **`claude-haiku-5-5` in the cheap slot**, replacing `claude-haiku-4-5`.
3. **Context discipline** — retrieval over large catalogs rather than dumping them.
4. **Only then** model slotting: Haiku 5.5 may take classification, end-of-turn and checks **where evals
   prove it safe**; the writer is not downgraded to save money.

`PRICES_USD` and the USD→INR rate are verified against published pricing and kept current; per-turn cost
is already recorded, and it feeds the Money board (ADR 0028).

**The training ban constrains the learning roadmap.** Meta forbids using WhatsApp data to create, develop,
train or improve any model, even aggregated, excepting work exclusive to one business. Signals keep being
recorded — required and harmless — but any learned model must be exclusive to the shop it came from.
Cross-tenant learned end-of-turn, timing or style models and cross-tenant bandit tuning are off the table.
Proposed to the founder as INV-13.

## Consequences

- Model cost falls substantially before any quality trade-off is considered, which is what makes the
  "quality first" ordering affordable rather than aspirational.
- Caching constrains prompt construction: the stable prefix must be byte-stable, so no timestamps or
  unsorted JSON in it. That is a real discipline for prompt authors and needs a test.
- A bigger eval suite costs real money to run against real models and becomes the gate on every prompt
  change. That is the intended trade.
- The sales-effectiveness graders are subjective and will need iteration; starting conservative and
  growing from real pilot failures is the plan already in CLAUDE.md.

## How to revisit

Once real-model evals and cost per conversation are measured (Phase 2), record the chosen model per
pipeline stage and the measured numbers in a superseding ADR. If measured quality at Haiku 5.5 matches the
strong model on the writer for some business types, that is a measurement, not a guess, and the slot can
change.
