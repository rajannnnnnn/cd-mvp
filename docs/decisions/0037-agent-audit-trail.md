# 0037 The agent audit: every AI run, recorded as it happens

**Status:** accepted · 2026-11-03 · founder: "in audit I most want the agent's internal routing and decisions, AI costing (all model calls consolidated), overall latency: what triggered it, until it is done with messages or with no reply"

## Context

`turns` records one committed decision cycle. It misses the runs that matter most when something is wrong: a run that was stopped
because the AI was not allowed to answer, one overtaken by a newer message (whose model cost was lost), one that failed, one that chose
silence, one whose worker died. It also does not say what woke the AI, and has no end-to-end time.

## Decision

A table, **`agent_runs`**, one row each time the AI is woken, **written when the run starts and finished when it ends**, so a run killed
halfway is visible (the recovery sweep marks it `interrupted`). It records:

- **Trigger:** turn detection (with why it judged the customer done, how long it waited, how many messages) or the scheduler.
- **Routing and decisions:** intent, directives, hand-offs, lead stage, language, the kind of each pricing decision (never an amount), checks that failed, a fallback if a safe holding reply was used, and **which tier made each decision** (rules or a language model).
- **Cost:** every model call of the run consolidated and split by step, with tokens, milliseconds, cache use and money; **kept even when the run is discarded**.
- **Outcome:** replied, reaction only, no reply (and why), blocked (by which rule), superseded, failed.
- **Latency:** the agent's own time, and, joined from the messages, the customer's wait from their first message to the first and last reply.

The founder reads it on the admin plane as a summary (outcomes, cost per run and per reply, **waste** = cost of runs with no reply, latency
percentiles, premature-run rate), a filterable list, and a per-run timeline. It carries **no customer words, name or number and no price
or floor** (a test scans it). It is a shop table under row-level security, exported and erased with the shop, and subject to the turn retention setting.

## Consequences

- "Why didn't the AI reply?" always has an answer, and "where does the money go" is a query.
- Costs of discarded work are visible, which is what makes turn-detection and tiering decisions measurable.
- One more row and two small writes per run, off the customer's critical path; a failure to record is logged and never stops the turn.

## How to revisit

If per-run rows become a volume problem, keep them for the retention period and roll older ones up into daily aggregates; the summary is
already computed from aggregates of the same columns.
