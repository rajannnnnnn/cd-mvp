# 0007 LLM pipeline, vendor interface, and the deterministic local stand-in

**Status:** accepted · 2026-10-04

## Context
No API keys exist yet, and the product must still run end to end and be tested without mocks of product behaviour.
LLM vendor and models must be configuration with a fallback.

## Decision
- `LLMProvider` interface (`generate(request, schema) → result with tokens/latency`). Implementations: **Anthropic**
  (official SDK, structured output through `messages.parse`, model IDs from configuration) and **local**
  (`LLM_PROVIDER=local`): a deterministic, rule-based NLU + response renderer that implements the same interface.
  It is a stand-in for the *model*, not for product pieces: everything around it (pricing engine, reply checks,
  context assembly, delivery, handoffs) is the real code. Configuration validation **refuses** the local provider and
  the simulator in `ENV=production`.
- Pipeline per turn: planner (intent, lead stage, proposed action, structured) → pricing engine executes any price
  request → writer phrases the reply from cleared values → reply checks (issued prices only, scope, no human claim,
  no unbacked urgency, CR-8) → one regeneration, then a safe holding message plus handoff → commit under a row lock that
  re-checks the conversation version (INV-7).
- `ResilientLLM` chains providers with a circuit breaker; provider failure ends in a holding message + handoff, never silence.
- Prompts are versioned files (`prompts/*.vN.md`); each turn records prompt versions, models, tokens, cost, latency.

## Consequences
- Co-founders can see a faithful product today; the stand-in's language quality is intentionally modest (it is improved
  by reading real seeded transcripts, see PROGRESS).
- **Not yet done:** the conversation evaluation suite against real models (needs an API key) and measured cost per
  conversation. Anthropic adapter is tested against a local fake Messages API (wire format, errors, retries).

## How to revisit
When keys exist: run the eval suite per model, choose models by safety first then cost/latency, record in a new ADR.
