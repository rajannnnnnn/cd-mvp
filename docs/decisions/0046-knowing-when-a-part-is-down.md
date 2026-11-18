# 0046 Knowing when a part is down

**Status:** accepted · 2026-11-08 · founder: "build it" (alerts for a server or part going down)

## Context
Founder alerts (ADR 0029) fire when a component records a problem: a failed turn, a failed send, dead-lettered jobs, a support
ticket. Nothing fired when a process simply stopped. A stopped gateway, agent worker or API leaves no error behind, the oldest
queued job's age was only a metric, and if the web scheduler itself stopped, no alert of any kind could go out, because it is
the component that sends them.

## Options
1. **An outside uptime checker only**, polling each part's `/healthz`. Covers a dead machine, but the internal parts are not
   reachable from outside (only `receive`, the site and the console are published), so it cannot see a stopped worker.
2. **Heartbeats in the database, watched by every scheduler**, plus a queue-stall check. Sees every process, including ones that
   are not published. Cannot tell anyone when the web scheduler or the database is the thing that died.
3. **Both: heartbeats and stall checks inside, and one outgoing ping to an outside checker** that alerts when the pings stop.

## Decision
Option 3.
- Every process (api, ingress, admin, workers, schedulers) writes a heartbeat row every `HEARTBEAT_EVERY_S` (30 s) under a
  readable unit name: `gateway receive`, `gateway inbound`, `gateway send`, `agent worker`, `web api`, `admin api`,
  `<component> scheduler`. Replicas share a unit, so a unit is down only when none of its copies is beating.
- Every scheduler checks the heartbeats each half minute. A unit last seen more than `PART_DOWN_AFTER_S` (120 s) ago, but
  within the last 24 hours, raises a `part_down` alert: critical when customers go unanswered (receive, inbound, send, the
  agent worker, the all-in-one process), high otherwise. When it beats again the alert resolves itself, and the founder gets the
  recovery notice. A unit unseen for a day is treated as retired and no longer alerts.
- The same check raises `queue_stalled` when a queue's oldest due job has waited longer than `QUEUE_STALL_ALERT_S` (300 s):
  critical for `inbound.events` and `conversation.events`, high otherwise; it resolves itself when the queue catches up. This
  catches a consumer that is alive but stuck.
- After each successful alert run, the web scheduler posts to `ALERT_HEARTBEAT_URL` if set. Any dead-man's-switch service
  (healthchecks.io, Better Stack, Cronitor and similar; free tiers exist) alerts the founder when the posts stop: the machine,
  the database or the web scheduler is down. Empty means off.

## Consequences
- One new table, `heartbeats` (one row per unit, platform role only). Database tables: 50.
- The founder signs up for one outside checker and sets `ALERT_HEARTBEAT_URL` in `deploy/web/.env`; this is on the go-live list.
- A stopped part is reported within about 2.5 minutes; a stuck queue within about 5.5 minutes.
- Heartbeats depend on the database. When the database is down, nothing inside can report it; the outside checker does.

## How to revisit
If the outside checker proves noisy, raise its grace period before changing the code. If a part is retired for good, delete its
row from `heartbeats` (or wait a day). To check the outside path, stop the web scheduler for longer than the checker's period.
