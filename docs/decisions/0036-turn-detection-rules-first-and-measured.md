# 0036 Turn detection: rules first, measured, with a guard before the expensive step

**Status:** accepted · 2026-11-03 · founder: "make turn detection so accurate that agent cost is under control and UX is prioritised; I don't know whether it should be ML, rules or a small model: decide"

## Context

Turn detection decides when a customer has finished writing so the AI can answer once, to everything. It is wrong in two ways, and
each costs something different:

- **Too early** (the customer is still typing): the AI starts, the customer writes again, and either a stale answer is thrown away
  (model cost paid for nothing) or an answer that ignores half the message is sent (bad UX).
- **Too late**: the customer waits after they have finished; with the read and typing delays on top, a reply that should take five
  seconds takes fifteen.

A WhatsApp customer writes in bursts ("hi" / "do you have" / "silk sarees?"), in two or three languages, with rhythm that differs by
person. Today it is a heuristic: is the last message complete, plus the customer's own typing gap, bounded by a maximum wait.

## Options

1. **Keep the heuristic, tune the numbers.** Free and instant; accuracy limited by what a rule can see.
2. **A model on every message** (a language model, or a small classifier). Better at "is this sentence finished" in any language, but it
   adds a call, latency and money to every single message, including the ones where the rules are already sure. That is the opposite of
   the goal.
3. **A learned predictor per shop.** The right long-term answer, and what the interface already allows, but there are no pilot chats
   to learn from yet, and Meta's rules limit any learning to one business's own data.
4. **Rules first, measured, with a model reserved for the genuinely unclear band; plus remove the cost of being wrong.**

## Decision

Option 4. Decided in this order:

1. **Measure before and after.** A labelled benchmark of customer bursts (single messages, fragmented sentences, a greeting then the
   question, voice notes, photos then a caption, slow and fast typists, English and Hinglish) with timings. Two numbers: how often the
   AI starts before the burst is over, and how long the customer waits after the last message. Every change to the rules must improve
   the first without hurting the second. The corpus is hand-written and small; it is a regression gate and a starting point, to be
   replaced by real pilot data, not a claim about production accuracy.
2. **Better rules, using what the system already knows:** sentence-final words in Hinglish ("chahiye", "hai", "batao") count as complete; a
   short reply to a question the AI just asked is complete ("red"), where the same words alone would not be; customers in rapid-fire
   mode get a longer quiet period; a photo alone is followed by a caption surprisingly often, so it waits a little; voice notes are
   whole thoughts. Thresholds stay per-shop configuration.
3. **Remove the cost of being wrong rather than only reducing how often.** The conversation version is checked again after planning and
   before writing (done): if the customer wrote in between, the run stops before the writer, the more expensive step, and its planner
   cost is recorded as waste in the audit. The audit also records every run's *premature* flag (the customer wrote again within 20 s of
   the AI starting), so accuracy is a live number on the founder's board, not an opinion.
4. **A small model only for the unclear band, and only if the live number says the rules are not enough.** It sits behind the existing
   `EndOfTurnPredictor` interface, is consulted only when the rules' confidence is low, and must beat the rules on the benchmark and on
   the live premature rate before it ships. Not built now.

## Consequences

- Turn detection stays free and instant for the large majority of messages and adds no model cost.
- A wrong guess costs one planner call at most instead of a planner plus a writer.
- "How accurate is it" has an answer in the audit from the first pilot day.
- The labelled corpus is only as good as its author; real chats must replace it as soon as pilots exist.

## How to revisit

If the live premature rate stays above a few percent after the pilot has volume, collect the bursts it got wrong and train the per-shop
predictor on them (per shop only), or add the small model for the unclear band. If customers wait visibly longer than they need to,
lower the quiet thresholds for complete messages first.
