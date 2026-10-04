# 0009 Channel adapters and the signed-webhook simulator

**Status:** accepted · 2026-10-04

## Context
No Meta account exists yet (the partnership firm is being formed), but the whole product must run and be tested, and
conversation logic must never depend on WhatsApp-specific types.

## Decision
- `MessagingChannel` interface (send text/reaction/template, mark read + typing, download media). Implementations:
  **WhatsApp Cloud API** and **simulator**. Conversation, sales and agent code import only channel-neutral types.
- The simulated "WhatsApp network" does not shortcut the system: it builds **correctly formed, HMAC-signed Meta webhook
  payloads** (messages, owner echoes, delivery/read statuses, account and quality events) and POSTs them to the **real
  ingress**, which verifies the signature, stores the raw event, enqueues and acknowledges (INV-6, NFR-1). Outbound
  messages are recorded in a ledger that the dev chat view and the Playground read.
- `POST /sim/*` exists only when `SIMULATOR_ENABLED` and is refused in production by configuration validation.

## Consequences
The same code path runs in demo and production. **Payload fixtures are synthetic**, written from Meta's documentation.
They must be replaced or supplemented by payloads recorded from a real Meta test number as soon as one exists
(contract tests then run against recordings, as the Technical Design asks).

## How to revisit
Add Instagram/web chat by implementing the interface and an ingress parser in `channels`.
