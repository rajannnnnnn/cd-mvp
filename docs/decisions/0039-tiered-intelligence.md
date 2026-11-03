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
