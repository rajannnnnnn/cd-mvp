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

## Implementation notes (2026-11-03)

- **Level-triggered, not event-patched.** Everything that changes a chat (a customer message, an owner reply from the app or the dashboard, a
  pause or resume, a hand-off created or resolved, a reply sent, a turn committed) announces `conversation.state_changed` in the same transaction.
  The agent's clock answers by recomputing, from the database, which timers the chat needs now (`timer_rules.desired`, pure rules, no model) and
  setting, moving or cancelling to match. A due timer is recomputed once more before it fires, so a timer set under old facts never fires under new ones.
  Web, gateway and agent therefore never call each other: they only announce.
- **Rules**, tested without a database (`tests/test_timer_rules.py`): follow-back at the shop's own gap, sooner for customers who usually answer fast (four times
  their usual wait, never inside 30 minutes), scaled by stage (interested 1×, negotiating ¾, ready to buy ½), moved to when the shop opens if the owner chose
  that, dropped if it would land outside the free window, once per customer message. Take-over at the end of any pause (owner reply, hand-off, manual),
  and at once when the pause is over and the customer's message has had no answer from anyone.
- **The take-over gap is closed:** messages set aside during a pause are re-opened and go through turn detection like any message; the audit shows the run as `takeover`.
- **Replaces** the nudge scan and its `nudge_sent_at` flag. The setting keeps its name (`nudge_after_minutes`, shown as "Follow up silent customers after").
- The recovery sweep re-plans every active or paused chat, so a lost timer is re-armed and a healthy one is untouched.
- Migration 0028 (`agent_timers`; the audit's trigger names become turn_detection, follow_back, takeover).

