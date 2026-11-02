# 0025 Three components, and the contract between them

**Status:** accepted · 2026-11-02 · supersedes the component sketch in ADR 0020 by extending it to the whole system

## Context

ADR 0002 made the backend a modular monolith: one image, one codebase, roles selected by configuration.
ADR 0020 pulled the WhatsApp side into a named gateway (receive / inbound / send) and isolated the Meta
tokens to `send`. The founder's intended shape goes further: three components that never share a process
and meet almost entirely in the database —

- **web** — the frontend and the backend that serves it; speaks to the database and to nothing else.
- **gateway** — deals only with Meta: receives, hands messages to the agent, sends what the agent asks.
- **agent** — the ChatDeal agent: turn detection, planner, pricing engine, writer, checks, tools, and its
  own scheduler. Takes messages from the gateway and wake-ups from its scheduler.

The rule the founder stated: the agent never talks to the web backend, and the web backend never talks to
the agent. Owner notifications are database rows the web polls.

Today all three live in one image under different roles, and the web API still contains webhook intake and
a direct Meta call for connecting a number.

## Options

1. **Keep one image, enforce the split only by boundary checks.** Cheapest, but every container carries
   every secret and every dependency, and a break-in anywhere reaches everything.
2. **Three packages, three images, one shared library.** More to build and operate; each component carries
   only its own secrets and dependencies; a boundary can become a network boundary later.
3. **Full services with APIs between them.** Over-built for v1 and forbidden by the over-building guardrail.

## Decision

Option 2. Four deployables: **web**, **gateway**, **agent**, and a **migration job**, over a shared library
(database access, settings, crypto, queue, logging) that imports none of them.

**Table ownership** — exactly one component writes each table; others read, or use a named mailbox table.
web owns shops, catalog, settings, billing, support, sessions. gateway owns raw webhook events and the send
outbox. agent owns conversations, turns, handoffs, notifications, follow-ups.

**Allowed calls.** gateway → agent ("a message arrived") and agent → gateway ("send this"), each writing a
durable outbox row before handing over, so nothing is lost if the other side is down. **No other direct
calls.** web and agent have no path to each other, enforced by import rules in CI.

**Three questions the component map left open, now decided:**

- **Owner alerts and the daily summary go to WhatsApp as well as the app.** FR-HO-1 and FR-RP-1 require it,
  so it was never mine to drop. They are template messages: billed, and needing Meta approval. Handoff
  alerts are batched with a per-hour cap.
- **web never calls the gateway.** The earlier draft allowed a direct call for sign-in codes, connecting a
  number and owner replies. Rejected: it would put Meta reachability back into web and waste what ADR 0020
  bought. web writes a request row; the gateway acts on it. The sign-in code pays a sub-second delay.
- **Two schedulers**: follow-ups and summaries with the agent, billing and cleanup with web.

**Migrations** become their own component and image, the only holder of schema-owner credentials, run
before any component starts. Each component declares the lowest schema version it needs and refuses to
start below it. Changes stay backward compatible for one release so components can deploy in any order.

## Consequences

- Each component carries only its own secrets: the agent never holds the sign-in key, the gateway never
  holds the model key, web never holds a Meta token.
- Per-component database roles become possible, which is what keeps the floor prices (INV-1) out of reach
  of web and gateway.
- More images and compose files to operate; `scripts/stack.sh` and `deploy/` already assume per-component
  stacks, so this is incremental.
- The one-image deployment (HLD topology A) still works: the components can run on one machine.
- Moving the webhook intake and the Meta number-connect call out of the web API is a real code move, done
  one module at a time with the tests green after each.

## How to revisit

If operating four deployables proves heavier than the isolation is worth at pilot scale, collapse web and
its scheduler back into one image — the import rules and table ownership survive that. Do not collapse the
gateway or the agent back into web: token isolation and model isolation are the point.

## Implementation notes (2026-11-02)

**Reconciling this ADR with the Technical Design.** The Technical Design requires *one backend build artifact that runs in
different roles*; this ADR proposed three packages and three images. Dropping that property is the founder's call, not
mine, so what is built keeps one codebase and one artifact. A component is a **set of modules plus the roles and queues
that run them**, enforced by the boundary check (rule B9), and each component deploys on its own from the same artifact
with only its own secrets (ADR 0020). The founder still gets what the split was for: clone, configure and launch only the
parts needed, and scale them separately. Truly separate images with separate dependency sets remain possible later.

**Measured before changing anything:** only four imports crossed the lines, not the six first feared (the webhook routes
and the delivery planner were already on the right side). They are recorded in `KNOWN_CROSSINGS` and removed one at a
time; a new crossing fails the build, and so does a listed one that no longer occurs.

**Sign-in codes and number connect now go through a request the gateway answers** (migrations 0017 and 0018):
- The web side writes a request and waits for the answer (`salesai/gateway_wait.py`, one helper for every such request).
  In production the gateway is another process and the wait is a short poll; tests run the real send queue inline.
  Everything the owner sees is unchanged: the same responses and error codes (the nine existing connect tests and the
  auth tests pass untouched), and a sign-in failure still reaches the person waiting for the code.
- If the gateway does not answer in time the request is marked failed in a way that cannot race the gateway, the owner is
  told, and a gateway that comes back later finds nothing to send, so no stale code or half-finished sign-up appears.
- **Secrets in the request rows:** the plain sign-in code (and Meta's single-use authorization code) has to reach the
  gateway, so each is held in its request row only until used, never in an event or job payload (only the id travels),
  and cleared on success, failure, expiry, when the web side gives up, and by the scheduler for requests the gateway
  never reached. The plain code therefore lives at most one code lifetime. A database-level attacker already holds far
  more than a five-minute code, so this is judged acceptable; an encrypted hand-off is a possible later hardening.
- **Owner alerts and the reading of Meta's webhook now follow the same shape** (migrations 0019 and 0020): the agent records
  an owner message and the gateway sends it; the gateway parses each webhook into channel-neutral events and hands them to the
  agent through a `channel_events` mailbox, queuing only the event's id. The agent no longer imports anything from the
  gateway, and `KNOWN_CROSSINGS` is empty. A new queue, `conversation.events`, is the agent's intake.
- **What the split bought, concretely:** the agent's workers (`conversation.events`, `conversation.turns`,
  `owner.notifications`, `platform.events`) no longer need the Meta master key; only the gateway's send queue does
  (`SENDING_QUEUES`). A retry of one owner's alert no longer replays everyone's. The 24-hour window is re-checked at send
  time from the database for owner messages too.
- **Not yet done:** the web API role still holds the master key, because the operator's manual pilot registration of a
  number (FR-ON-3) encrypts a pasted token itself. That path retires when Tech Provider approval makes Embedded Signup
  the only route; until then web cannot drop the key. Per-component database roles, and per-component compose files for
  web and the agent, are the next steps of this ADR.
- **One scheduler per component, not two** (`SCHEDULER_COMPONENT=web|gateway|agent|all`). This revises the "two schedulers"
  line above: the gateway also has timed work (its raw-request log), and a part that cannot tidy its own data would need a
  neighbour's timer to run. Each component's scheduler moves the outbox along (safe in parallel: rows are claimed with
  `SKIP LOCKED`), expires queue leases, publishes its queue gauges, and does only its own duties: web = billing, trials,
  platform alert delivery, sign-in and sign-up data; agent = daily summaries, follow-ups, conversations and AI records;
  gateway = the raw-request log. Each holds its own advisory leader lock, so all three are leader of their own work at once.
  `all` keeps the old behaviour for development and tests.
