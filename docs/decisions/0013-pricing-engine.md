# 0013 Pure pricing engine and the concession schedule

**Status:** accepted · 2026-10-04 · INV-2, INV-3, INV-4

## Decision
`modules/pricing/engine.py` is deterministic and has no I/O (`decide(request) → decision`; time is an input; imports
are stdlib-only, enforced by boundary rule B6). Money is `Decimal`, two places. It returns the exact set of values it
issued so the reply check can enforce INV-2. Disclosure modes (fixed, range, starts_from, after_qualifying,
on_request), negotiation only when both flags are set, concession steps with optional requirements (quantity, advance
payment, repeat customer), "never offer less than the customer already offered", "hold once at the floor then hand
off", offers applied only when active, in date range and conditions met; only an owner-marked `may_cross_floor` offer
may go below the floor.

**Schedule:** evenly spaced steps from list price to floor, each rounded **up** to the business's rounding rule, strictly
decreasing, the last step is the floor (starting point from the Technical Design; chosen because it is explainable to
owners and cannot undercut the floor by rounding). Property-based tests (hypothesis) cover all properties and were
mutation-checked (deliberately broken engine variants make them fail). A golden file ties the marketing demo (JS) to the
engine (`tests/golden/ladder.json`).

## How to revisit
Front-loaded concessions or learned schedules can replace `concession_levels` behind the same tests; evaluate with the
conversation evals and pilot outcomes.
