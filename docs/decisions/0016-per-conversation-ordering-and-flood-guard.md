# 0016 Outbound actions are ordered per conversation; per-conversation AI turn budget

**Status:** accepted · 2026-10-04

## Context
A conversation load test reported a message-to-first-reply p95 of 105 s. Cause: outbound actions (read receipt, typing,
each message part) were queued with the ordering key `num:<number id>`, so every action for one WhatsApp number ran strictly
one after another. A single customer sending 1,500 messages left ~1,500 replies queued ahead of every other customer on
that number, which contradicts the Technical Design rule that different conversations run in parallel (NFR-4).

## Decision
1. Outbound actions use the ordering key `conv:<conversation id>`: order matters within a conversation, nowhere else.
   The per-number token bucket (5 messages/s, Meta's throughput for coexistence numbers) stays shared and is the only
   cross-conversation limit; actions over budget are deferred, not dropped.
2. A flood guard: after `MAX_AI_TURNS_PER_CONVERSATION_10MIN` (default 30) AI turns in one conversation within ten minutes,
   further customer messages are recorded but not answered until the window passes. This bounds the AI cost one customer
   can cause and removes the noisy-neighbour case. The default is far above any real negotiation.

## Consequences
Replies to different customers of one number no longer block each other. A customer who genuinely sends dozens of
messages in minutes gets no further AI replies for a while (visible in the chat; the owner can take over).

## How to revisit
Tests: `test_outbound_actions_are_ordered_per_conversation_not_per_number`, `test_a_flooding_customer_is_recorded_...`.
Raise or remove the limit by configuration. Re-run `loadtest.run conversations` on a clean database to re-measure.
