# 0040 Daily summaries are removed

**Status:** accepted · 2026-11-03 · founder decision, product behaviour: "there are no daily summaries, avoid that"

## Context

PRD requirement FR-RP-1 asked for a daily WhatsApp summary to the owner, and earlier decisions (ADR 0025, ADR 0031, ADR 0026) built on it:
a scheduler duty, an owner-notification purpose, an approved message template, and the idea that learned-fact proposals would ride the summary.

## Decision

The founder decided there are no daily summaries. The feature is removed: the scheduler duty, the notification purpose and template, the
event, the setting, the tests and the documentation that mention it. **This supersedes FR-RP-1.** The owner still sees numbers in the app's
analytics screens, and still gets immediate alerts for hand-offs and deals. The learning loop's proposals (ADR 0031) no longer wait for a
summary: when the time comes they are asked on their own, at most a few a day, per owner setting.

## Consequences

- One fewer message template to get approved, and one fewer paid owner message a day per shop (ADR 0026's channel budget improves).
- The owner has no push report; if shops ask for one, it returns as an opt-in feature with its own decision.

## How to revisit

If pilots show owners want a periodic report, bring it back as an owner opt-in in the app first, WhatsApp second.
