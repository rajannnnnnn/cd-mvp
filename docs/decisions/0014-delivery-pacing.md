# 0014 Human-like delivery planner and send-time checks

**Status:** accepted · 2026-10-04

## Decision
A reply is turned into **timed actions** (read receipt, typing indicator, each part after a typing time and gap).
Durations are drawn from **log-normal distributions whose parameters are stored per business** (`businesses.timing_params`),
clamped, with a total-delay cap, so no two replies share a rhythm and each business can be tuned. The owner picks a speed
preset (quick / natural / relaxed) in Settings; the signals needed to learn timing later (read/reply timings from echoes,
customer gaps, statuses) are stored from day one. Every scheduled send re-checks, **at send time**: conversation
version (INV-7, except the owner's own messages), the 24-hour window (INV-8), AI pause, opt-out, personal contact (INV-10), number status. A per-number token bucket limits outbound
sends (`Defer` re-queues without consuming an attempt). Business-hours behaviour is a business setting.

## How to revisit
Replace the priors per business as data accumulates, or add the v2 bandit tuner behind the planner interface.
