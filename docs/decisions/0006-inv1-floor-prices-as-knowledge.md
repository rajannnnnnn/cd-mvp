# 0006 How INV-1 treats a floor that surfaces as an issued quote

**Status:** accepted pending founder confirmation · 2026-10-04 · **needs a founder decision**

## Context
INV-1: floor prices must not appear in LLM context, API responses, logs, analytics or exports. But the pricing ladder
ends *at* the floor (the last concession step is the floor, by the "concede gradually" requirement). When the assistant
finally says "₹7,650 is my final price", a number equal to the floor is stated to the customer, and the owner sees it
in the conversation transcript.

## Options
1. **Floor is secret as knowledge, not as a number once it has been issued as a quote** (chosen).
2. Never end the ladder at the floor (always stop above it) so the floor is never revealed even as a final price.
3. Treat every issued quote that equals the floor as sensitive and redact it from the owner's transcript.

## Decision
Option 1. What is protected is *the fact that this number is the owner's limit*. Concretely:
- The floor value is never given to the LLM, never returned by any endpoint (`floor_set` only), never logged, never in
  exports or the audit log (tests check API responses, the OpenAPI contract, exports, the audit log and the LLM input).
- The planner never sees prices at all: amounts are redacted in the AI's view of history; the writer sees only
  engine-issued values for that turn. The directive label is neutral (`owner_review`), not `below_floor`.
- The assistant never says that a number is a floor or a limit. A final step is phrased as a final price.
- Reply checks (INV-2) reject any amount the engine did not issue in that turn.

## Consequences
A persistent customer who bargains to the end learns the lowest price the owner will accept, which is inherent in any
negotiation that ends in a sale at that price. If the founder prefers that the ladder never lands exactly on the floor,
option 2 is a one-line change in `concession_levels` (stop at the last step above the floor) plus the golden test.

## How to revisit
Founder: confirm option 1, or ask for option 2/3. Tests that encode this: `test_pricing_engine.py`,
`test_catalog_pricing_service.py`, `test_conversation_flow.py` (no floor in LLM input), `test_api.py` (no floor in any response).
