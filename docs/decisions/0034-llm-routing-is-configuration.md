# 0034 LLM vendor, model and effort per stage are configuration; gpt-oss-20b is the default

**Status:** accepted · 2026-11-02 · founder: "make the agent easily configurable for LLMs so in future if we wanna
switch we switch", and "I prefer GPT 20B for its cheap and quality, so let's go with that unless you tell it won't work"

## Context

ADR 0007 put the model behind an interface, and the model per stage was already a setting. Switching was not easy
in practice, for four reasons found in the code:

1. **Only one real vendor existed** (Anthropic), so the founder's choice, `openai/gpt-oss-20b`, could not be used.
2. **A model missing from the hard-coded price table was recorded as free.** `cost_micros_inr` fell back to zero
   with no error, so switching to a new model would quietly make every cost and margin figure wrong.
3. **Fallback was per vendor and reused the primary's model name**, so a cross-vendor fallback would have sent a
   gpt-oss model name to Anthropic and failed exactly when it was needed.
4. **Effort was hard-coded** in the Anthropic adapter, not configurable per stage.

**Does gpt-oss-20b work here?** Yes, with one risk that must be measured rather than assumed.

- Safety does not depend on the model. Prices come from the pricing engine (INV-2), floors never enter model
  context (INV-1), and every reply passes deterministic send-time checks. A weaker model cannot quote an
  unauthorized price because the code will not let it.
- Cost and speed are strong: about $0.075 / $0.30 per MTok on Groq at roughly 1,000 tokens/sec, against $2 / $10 for
  Sonnet 5.5. Haiku 5.5 is close in price ($0.10 / $0.50), so it is the honest same-price comparison.
- The risk is writing natural, persuasive Hinglish (and later Tamil, Tanglish and other Indian languages). OpenAI's
  model card puts gpt-oss-20b at 70 / 77 / 79 on Hindi MMMLU at low / medium / high effort, but MMMLU is
  multiple-choice comprehension in Devanagari, not generation and not code-mixed text. An independent evaluation
  notes multilingual weaknesses. No Hinglish sales-writing evidence exists either way.

(All figures from secondary sources: OpenAI's model card and price aggregators. Groq's own docs and Meta's were not
reachable from this environment.)

## Decision

**Routing is configuration**, validated at startup:

- `LLM_PROVIDER` sets the vendor for every stage; `LLM_PROVIDER_PLANNER/WRITER/CHECK` override one stage.
- `LLM_MODEL_*` and `LLM_EFFORT_*` per stage.
- `LLM_FALLBACK_PROVIDER` with `LLM_FALLBACK_MODEL` — the fallback runs its own model.
- `OPENAI_COMPAT_BASE_URL` picks the host. One adapter covers Groq, Together, Fireworks, OpenRouter and a
  self-hosted vLLM, so moving host — including to our own GPU in an Indian region later — is a URL change.
- `LLM_PRICES` adds or overrides prices as data, so a new model or a host's different rate needs no code.

**Startup refuses to run quietly wrong:** a vendor in use without its key; a non-`claude-` model on Anthropic; a
malformed `LLM_PRICES`; any configured model with no known price; the local stand-in on any stage or as the fallback
in production.

**A new vendor is one adapter file plus one line** in `BUILDERS`. Vendor HTTP stays inside adapter files, enforced
by the boundary checker (B4).

**Default:** `openai/gpt-oss-20b` on every stage. Effort **medium** for planner and writer — low to medium is the
large Hindi step (70 → 77); high adds two points for more latency and output tokens — and **low** for the check,
which is a narrow classification.

**The OpenAI-compatible adapter is defensive** because host behaviour varies and could not be checked against each
host's documentation: JSON is requested by schema (or plain JSON mode via `LLM_JSON_MODE`), every reply is validated
here regardless, a reasoning model's thinking ahead of the answer is tolerated, and a reply cut off at `max_tokens`
says so instead of failing vaguely.

## Consequences

- Switching is an edit and a restart. The likely first switch, if the writer's Hinglish proves weak, is
  `LLM_MODEL_WRITER` alone — to gpt-oss-120b or a stronger model — leaving the cheap model on the other stages.
- With gpt-oss, model cost falls to an estimated ~₹0.2 per conversation, against ~₹2.1 on Sonnet. That makes
  Meta's per-message fee the largest variable cost, which raises the stakes on adaptive message splitting
  (ADR 0026) and on confirming Meta's service-message pricing.
- **`max_tokens` needs headroom on a reasoning model.** The planner (1,200) and writer (900) budgets were sized for
  answers alone; gpt-oss spends part of the budget thinking. A truncated reply fails safely into a handoff
  (INV-12), and the benchmark reports the truncation rate so the budgets can be tuned from data.
- Customer conversations go to whichever host is configured. Under DPDP and Meta's AI Providers clause the host
  must not train on the data and the cross-border position must be understood. Open weights keep self-hosting in
  India available as the strongest answer.
- Languages are mostly decoupled from the model, with one known exception: the planner's schema lists only
  `en / hi / hinglish` and scripts `latin / devanagari`, and the reply check knows only those two scripts.
  Widening them for Tamil, Tanglish and others is a small, contained change plus eval scenarios; deferred by the
  founder.

## How to revisit

Run the benchmark (`backend/scripts/benchmark_models.py`) once an API key exists, and choose per stage on safety,
then selling quality, then latency, then cost. Record the choice and the measured numbers in a superseding ADR.
