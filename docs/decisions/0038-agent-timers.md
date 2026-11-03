# 0038 Agent timers: the scheduler is the agent's own clock

**Status:** accepted · 2026-11-03 · founder: "the scheduler is for the AI agent: for this message, if the customer did not reply, follow up at this time; if the owner has taken over, check after this time (auto take-over); and these change with subsequent triggers: the owner messaged, the user replied"

## Context

Two time-based behaviours existed as scans, not as plans: a follow-up "nudge" found by a periodic query over quiet conversations, and an
AI pause after the owner replies that simply expired. Two gaps follow. A pause ending wakes nobody: a customer who wrote during the
owner's takeover was never answered afterwards (their messages were recorded and marked handled). And nothing says *why* a follow-up
is due, or reacts to what happened since.

## Decision

**One durable timer per kind per conversation, set, replaced or cancelled by each event, fired by the agent's scheduler.**

| Kind | Set when | Due | Cancelled / moved when |
|---|---|---|---|
| `follow_back` | the AI replied and the lead is engaged | the customer's own reply rhythm, scaled by lead stage, moved to business hours, always inside the 24-hour window | the customer writes (they replied); the owner replies; AI off |
| `takeover_check` | the owner replied from their own app, or a hand-off paused the AI | when the pause ends | the owner writes again (moved later); hand-off resolved or AI resumed (fires now if the customer is waiting) |

At `takeover_check`, if the customer's last message has had no answer from anyone, the AI takes the conversation back and answers; if the
owner is active, the check moves. A fired timer enters the **normal turn path**, so every rule (INV-7, INV-8, INV-10), the audit and the
recovery sweep apply to it unchanged. Timers are rows, so a restart loses nothing; the recovery sweep re-arms any that were missed.

Timing is rules, not a model: the customer's own median response time in this chat, the stage, the business's hours. Each timer records why
it is due when it is (a short explanation, no customer content), shown in the audit.

## Consequences

- The take-back gap is closed: no customer is left unanswered after an owner stops replying.
- Follow-ups adapt to each customer and to events instead of firing on a fixed clock.
- Replaces the nudge scan; removes the `nudge_sent_at` flag's role.

## How to revisit

If follow-ups annoy customers, widen the rhythm multiplier or cap them per customer; if owners want a different take-back delay, it is the
existing owner-pause setting. A second follow-back (not just one) would be a new timer kind with its own rule, not a flag.
