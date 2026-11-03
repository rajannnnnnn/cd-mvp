# 0039 Tiered intelligence: rules, then a small model, then the large model

**Status:** accepted · 2026-11-03 · founder: "use other models or rules whenever possible, not always blindly an LLM, subject to our performance and latency"

## Context

Priorities are security, then user experience (quality, then latency), then cost. A language model on every step is the simplest build and
the slowest and costliest, and many steps do not need it: a greeting, a thank-you, an hours question the shop's profile answers, a price
ask for a product named exactly, the checks that stop a bad reply, knowing when the customer has finished, when to follow up.

## Decision

A **cascade for each stage** (understand, write, check): a deterministic rule when it is *sure and safe*, a small model when the rules are
unsure and the task is narrow, the large model for real language understanding and for writing in the customer's own style. Each tier
returns a **confidence**; below its threshold the next tier decides. Rules may never decide anything about money: prices still come only
from the pricing engine (INV-2), whatever tier wrote the sentence.

- **Safe for rules:** turn detection, follow-up timing, greetings and acknowledgements, thanks, opt-out phrases, plain profile facts, a catalogue
  alias match for a price ask, all deterministic reply checks, language/script detection.
- **Small model (later, if measurements justify it):** intent when rules are unsure, the quality check on risky replies only.
- **Large model:** open questions, negotiation phrasing, anything that needs the customer's tone matched.

**Every decision records its tier** in the audit, so the founder sees where cost and latency go, and **a rule or small model is only
allowed to replace a model call after it matches or beats it on the evaluation scenarios** for that case (quality first, then latency, then cost). Tiers
can be switched off per stage by configuration, so a disappointing rule is one setting away from being bypassed.

## Consequences

- Cheap, fast answers for the common simple turns; the model is spent where it earns its cost.
- Replies from rules must still sound like the customer: templates are only for acknowledgements and facts, with the customer's style applied;
  anything that needs tone goes to the model.
- More moving parts: each tier needs its own evaluation, and a regression in a rule is a quality regression the evals must catch.

## How to revisit

Read the audit's per-stage tier split and per-tier quality. If a rule's wrong-decision rate rises, raise its confidence threshold or retire it; if the
model share stays high on steps that look simple, write the rule.

## Implementation notes (2026-11-03): the first slice

- **Planner.** `TieredPlanner` sits in front of the language model for the planner stage only. It runs the existing deterministic planner and keeps its answer when the intent is on the allow-list (`TIERING_PLANNER_INTENTS`, default: greetings, thanks, goodbyes, "are you a bot", "I want the owner", complaints, "not interested", photos and voice notes with no text, plain shop facts) and, for an ask about a product (price, availability, "tell me about it"), only when the customer's own words name that product exactly (its name or an alias, whole words, and no other product) or it is the product already being discussed. A category word, a colour, a typo, or no product goes to the model. Discounts, counter-offers, orders, visits, confirmations, small talk, scope calls and anything unclear always go to the model. A rules answer is recorded as provider `local` with zero tokens and zero cost, so the audit shows "rules" for that step.
- **Safety cases trust the rules because a false alarm is cheap and a miss is not:** a wrongly flagged complaint or request for the owner costs one alert; a missed one costs a customer.
- **Model check.** The optional model check ("is anything invented?") now runs only when the reply states something: a price, a fact, a product, stock, an order or visit summary. Greetings, thanks, "which product?", hand-off notices and the like skip it; a directive nobody classified is treated as risky. The deterministic checks always run.
- **Not done, deliberately:** the writer stays on the language model (replies must match the customer's own way of talking, which a template cannot); negotiation and commitments stay on the model until measured; a small model for the middle band is not built.
- **How to widen it:** run `scripts/benchmark_models.py` with a real key to compare the rules' planner output with the model's on the evaluation scenarios, read the audit's per-step tier split in production, and add an intent to `TIERING_PLANNER_INTENTS` only when the rules agree with the model on it. The first live measure is how often an owner corrects a rules-decided reply.
- **Cost effect, estimated, not measured:** a typical greeting or thanks turn drops from three model calls to one; a named-product price turn from three to two. The audit's `by_stage` split will show the real figure.

