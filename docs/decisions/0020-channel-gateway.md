# 0020 Channel gateway: receive, inbound, send

**Status:** accepted · 2026-10-31 · covers NFR-1, INV-6, INV-11, INV-12 · relates to 0004, 0009, 0012

## Context
WhatsApp is the product's front door in both directions. Meta needs a fast acknowledgment (NFR-1) even when the
sales agent is slow, deploying or down. Only one part of the system should hold WhatsApp credentials (INV-11), and the
agent, which reads untrusted customer text and calls an LLM, should not be it. A customer message does not always
deserve a reply, and the agent may later decide to message first inside the WhatsApp window, so the code that talks to
Meta must not contain sales logic, and the sales logic must not talk to Meta.

## Options
- One bundle: gateway and agent in the same containers, scaled together.
- **Gateway as its own component with three parts; the agent is a separate component.**
- Gateway parts as three separate components, each in its own project.

## Decision
The channel gateway carries WhatsApp traffic and nothing else. It has no LLM, no pricing and no sales decisions. It
has three parts, each its own container started from the one backend image, differing only by role and queue:

| Part | Role / queue | Job | Database roles |
|---|---|---|---|
| `receive` | role `ingress` (public) | Verify the signature, store the raw event, enqueue, answer 200 | `app_system` |
| `inbound` | worker on `inbound.events` | Turn a stored webhook into clean rows (which business, which customer): messages, echoes, statuses, account events. Ignore duplicates. Hand the conversation to the agent (`conversation.turns`) | `app_user`, `app_system` |
| `send` | worker on `outbound.actions` | The only way out to Meta: check the conversation window and per-number limit, pace, send, record the result. The only part that holds WhatsApp tokens | `app_user`, `app_system` |

Not in the gateway, by decision:
- **Context preparation** (history, customer, state, shop facts, why the agent woke up) belongs to the start of the
  agent's turn. A scheduled follow-up has no webhook, so it never passes through the gateway but needs the same
  context, and the bundle must be built after the end-of-turn wait to cover every message the customer sent.
- **Whether to answer.** The gateway passes every message on; the agent chooses to reply, stay silent, schedule a follow-up
  or hand off.

Hard rules live at `send`, not in the agent: the window must be open, the per-number token bucket (`rate_buckets`,
shared across copies through the database) must allow it, pacing applies. The agent only asks.

The gateway does not scale with the agent. Copies of each part are independent and stateless; they share only the
database queue. `receive` scales on webhook latency, `inbound` and `send` on the oldest-job age of their queue.
A small deployment may run every part in one container (`role=all`); that is packaging, not design.

## Consequences
- Meta gets its acknowledgment while the agent is down, and queued work resumes when it returns.
- A break-in to the agent cannot reach the WhatsApp tokens, once the follow-up work below is done.
- Deployment: `deploy/gateway/docker-compose.yml`, project `chatdeal-gateway`, its own env file, only `receive` published.
- Follow-up work this decision requires, recorded here so it is not forgotten:
  1. **Raw log fidelity.** Store the exact request bytes, the signature header and the receive time, and keep requests
     with a bad signature (flagged invalid, size-capped, shorter retention). Retention becomes a setting
     (`RAW_WEBHOOK_RETENTION_DAYS`); today it is a fixed 30 days in the scheduler.
  2. **Per-role secrets.** Settings requires every secret in every container. `MASTER_KEY` and the token columns should
     be required and available only for `send`; `JWT_SECRET` and `OTP_SECRET` only for the API.
  3. **Agent reads only the columns it needs.** The context loader reads the whole `whatsapp_numbers` row, which
     includes `access_token_enc`.
  4. **Monitoring ports.** `inbound` and `send` expose `/metrics` on the container network only; publish for the monitor
     when it exists.
- **Follow-up status (2026-10-31): all four done and tested.**
  1. Raw log fidelity: migration `0012_webhook_raw_log.sql`; exact bytes, signature header and receive time kept; bad-signature requests kept as
     flagged evidence (never queued, size-capped by `INVALID_WEBHOOK_MAX_BYTES`, shorter `INVALID_WEBHOOK_RETENTION_DAYS`);
     `RAW_WEBHOOK_RETENTION_DAYS` replaces the fixed 30 days. Tests: `test_gateway_raw_log.py`.
  2. Per-role secrets: `Settings._required_secrets` requires each secret only for the roles that use it (`receive`: the two Meta values;
     `inbound`, the agent and the scheduler: none; senders, the API and `all`: `MASTER_KEY`; the API and `all` also `JWT_SECRET`/`OTP_SECRET`).
     A present secret is always validated; reading an absent master key raises; `TokenVault` without a key refuses to open tokens.
     `deploy/gateway/docker-compose.yml` hands each part only its own secrets. A worker is a "sender" when its queues include
     `outbound.actions`, `owner.notifications` or `platform.events` (all three send to WhatsApp), so the default all-queues worker still needs the key.
     Tests: `test_config_roles.py`.
  3. The agent's context loader and the turn planner select explicit `whatsapp_numbers` columns, never the token. Tests:
     `test_gateway_token_isolation.py` (source scan of the agent and conversation modules plus a behavioural check).
  4. `/metrics`, `/healthz` and `/readyz` were already served by every worker; now locked by `test_gateway_metrics.py` (queue depth, oldest-job age and
     dead letters for each queue; no webhook or API route on a worker). Not published to the host; publish for the monitor when it exists.
- Not covered by this decision and still open: `owner.notifications` and `platform.events` workers are "senders" but are not yet a named gateway
  part; today they run in the backend stack. When they move into the gateway they need `MASTER_KEY` too, as above.
- Already satisfied by the existing router and queue: event routing by type, duplicate protection by WhatsApp message ID,
  per-conversation ordering, a shared send-rate limit.

## How to revisit
If `inbound` volume tracks the agent so closely that a separate container adds only cost, run it in the agent's
container group (same role and queue, different packaging). If Postgres connections become the limit as copies grow,
move the queue to the Redis backend (ADR 0004) before adding more copies.
