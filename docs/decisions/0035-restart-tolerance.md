# 0035 Restart tolerance: what is in flight lives in the database

**Status:** accepted · 2026-11-03 · founder requirement: "restart properly, never stuck in the middle, kept in the database, not on disk and not in memory"

## Context

The agent is restarted whenever it is deployed, scaled or crashes. Whatever it was doing at that moment must neither be lost nor
left half-done, and a customer must never wait in silence because a process died. The system already kept its work in Postgres:
conversations and messages, the transactional outbox, and a job queue with leases and a reaper. Reading the code against the
requirement found these gaps:

1. A crashed worker's jobs came back only when a 120-second lease expired, plus a scheduler tick: up to two and a half minutes of silence
   for a customer after a crash.
2. A worker that was alive but slow could have its job taken, and a worker that woke up late could still complete, fail or kill a job
   that had since been given to someone else (the queue did not check who owned it).
3. A stop request waited for in-flight jobs with no limit, and a kill left them to the lease.
4. Nothing looked at the database's own truth. If the queue lost work (a wiped Redis, a lost job, a crash between two steps), a customer
   message stayed unanswered for ever, a planned reply stayed queued for ever, and a hand-off row between two components stayed
   unhandled for ever.
5. A planned reply whose send was requested twice would be sent twice.

## Options

1. **Rely on the queue's leases and retries only.** What exists; leaves 4 and 5 open and 1 slow.
2. **Make the queue itself the only durable truth and make it stronger** (acks, heartbeats, fencing). Fixes 1–3, not 4: it cannot notice
   work it never had.
3. **Both: a stronger queue, and a sweep that works from the database's own state and re-drives the difference.** More to build; covers
   every way work can go missing, and does not depend on the queue backend.

## Decision

Option 3.

**The queue gets short, renewed leases and fencing.** A job's lease is 30 seconds and the worker renews it every ten while the handler
runs, so a crashed worker's jobs return within about half a minute and a slow worker's never do. Every claim bumps a **fencing token**
that never goes down; renewing, completing, failing, deferring or superseding a job is a no-op for a worker whose token is not the latest,
and the heartbeat tells the stalled worker to stop. Same behaviour on the Postgres and the Redis backends, proven by the one shared
queue test-suite.

**A stop request is a hand-over, not a wait.** On stop, in-flight jobs get a grace period (default 20 s). Anything not finished is given
back immediately and **does not use up an attempt**, so a restart loop cannot dead-letter a conversation.

**A recovery sweep runs on the agent's scheduler, on start and every half minute, from the database's own state** (a conversation's
unanswered customer messages, a planned reply that is still queued, a mailbox row nobody picked up). It re-drives:

| What the database says | What the sweep does |
|---|---|
| a customer message unanswered for > 90 s with nobody working on it | requests an end-of-turn check again (bounded: a few tries, spaced out) |
| the same, after the last try | hands the chat to the owner (`system_failure` hand-off), marks the messages answered, alerts the founder (High): **never silence** (INV-12) |
| a reply planned > 2 min ago and still queued | requests the send again; if the customer has written since, cancels it instead (INV-7) |
| a gateway-to-agent event still pending, an agent-to-gateway owner message still queued | requests it again |
| a conversation stuck mid-step with nothing outstanding | sets it back to idle |

It never touches a conversation the AI must not answer (INV-10). Every repair is safe to repeat: an end-of-turn check for an answered
conversation finds nothing to do, and the sender now refuses to send a message that is no longer queued, so two requests for one reply
send it once.

## Consequences

- A crashed agent costs a customer about thirty seconds, not minutes, and a deploy costs nothing: in-flight work is handed back.
- Losing the queue entirely (including a Redis with nothing persisted) delays work by up to the sweep interval instead of losing it.
  With the default Postgres queue the queue's own state is already in the database; the sweep is what makes the *Redis* backend safe to
  run without persistence.
- At-least-once stays the delivery guarantee. A crash in the few milliseconds between Meta accepting a message and us recording it can
  still send that message twice; WhatsApp offers no idempotency key to prevent that, and a duplicate is judged better than a lost reply.
- Costs one more scan every thirty seconds per agent, over indexed conditions; it is limited to 200 rows per pass.

## How to revisit

If the sweep fires on a healthy system, the thresholds are too tight or a path is not clearing `answered`; fix the path, not the
threshold. If duplicate sends are ever measured, add a `sending` state claimed before the call and treat an unknown outcome as
hand-off rather than resend.

## Implementation notes (2026-11-03)

Migrations 0024 (job fencing token) and 0025 (recovery bookkeeping on conversations). `queue/worker.py`: heartbeat, lost-lease cancel,
release on stop. `modules/conversations/recovery.py`: the sweep. Settings `WORKER_LEASE_S`, `WORKER_SHUTDOWN_GRACE_S`, `RECOVERY_*`.
Tests: the shared queue suite (renewal, fencing, free release), `tests/test_worker_restart.py`, `tests/test_recovery.py` (including a worker
killed mid-turn: one decision, one answer).
