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
