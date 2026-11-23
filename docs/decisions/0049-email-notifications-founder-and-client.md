# 0049 Email notifications: one stream for the founder, one for clients

**Status:** proposed · 2026-11-23 · founder: "one for us founders, one for clients"; senders `founder-alert@` and `alerts@`; "design it properly". Extends ADR 0029 (founder email) and
changes one of its sentences (see Consequences). The client stream is product behaviour the PRD does not yet describe, so it is built only after this is approved.

## Amendment (founder, 2026-11-23) — this overrides anything below that disagrees
1. **One mail flow, two priorities.** Not two components. Every email, founder or client, goes through the same outbox and the same sender. The founder's mail is
   processed at the **highest priority**: the worker always takes due founder mail before any client mail, and a backlog of client mail can never delay a founder alert.
   Recipient rules, sender addresses and content rules stay separate (section 1 below); the pipeline is shared.
2. **Client mail has levels.** *Mandatory* mail cannot be unsubscribed from and has no switch: **invoices, renewal reminders and confirmations** (email address
   confirmation, and confirmations of changes to the account). *Optional* mail is a level that exists in the design (a per-kind switch, a one-click
   unsubscribe link, the `List-Unsubscribe` headers) but **no optional mail is built in v1**. There is no daily summary, usage warning, handoff or disconnect email.
   Those stay on WhatsApp and in the app. Mandatory mail carries no unsubscribe link, because there is nothing to unsubscribe from.
3. **Sender addresses are configuration**, never code: `ALERT_EMAIL_FROM` (`founder-alert@<domain>`) and `CLIENT_EMAIL_FROM` (`alerts@<domain>`). Any SMTP provider
   works. The provider decides what it accepts as a sender: Amazon SES only sends from an address or domain verified in SES (verify the domain once, with DKIM, and
   every address on it is allowed); Gmail and similar free providers will rewrite or refuse a sender that is not the logged-in account. Start-up and `doctor` send a test
   message from each address so a refused sender is found before launch, not by a customer.
4. **Email is formatted properly.** Every email has a plain-text part and a responsive HTML part from one template (brand header, one clear subject, short body,
   details as a small table, a footer with the sender's purpose and contact). Founder alerts use the same template with severity, the step or component, what happened, the
   fix and a link to the console. Rendering is tested (both parts present, no raw template text, header injection impossible) and checked visually in Mailpit.

## Context
- **Founder stream exists** (ADR 0029): `operator_alerts` -> `AlertRouter` -> `EmailSink` (plain SMTP) to `ALERT_EMAIL_TO`, with severity rules (critical at once and repeated,
  high at once then cooled down, medium hourly rollup, low daily digest, recovery notices). Its weak spot is coverage: model (LLM) failures do **not** raise an alert today. The
  pipeline logs the error, sends the customer a safe holding message and hands the chat to the owner, and the founder hears nothing unless the same chat fails repeatedly
  (`turn_failed`). A rejected API key, an empty credit balance or a model that cuts every answer off therefore goes unnoticed while every customer gets the holding message.
- **Client stream does not exist.** A shop owner is known only by phone number (`accounts.phone`); no email address is stored. Owners are told things over WhatsApp. If an owner's
  WhatsApp number is disconnected, WhatsApp cannot reach them at all.
- ADR 0029 says the founder email goes "explicitly not to the business owners who subscribe". That stays true of the founder stream. This ADR adds a separate stream whose only
  recipient is each shop's own owner and whose only content is that owner's own shop.

## Decision

### 1. Two streams that share a sender mechanism and nothing else
| | Founder stream | Client stream |
|---|---|---|
| Recipients | `ALERT_EMAIL_TO` (configuration only) | the owner users of that one shop, who have a confirmed email |
| From | `founder-alert@<domain>` (`ALERT_EMAIL_FROM`) | `alerts@<domain>` (`CLIENT_EMAIL_FROM`) |
| About | the platform: failures, down parts, queues, security | that shop: its own WhatsApp, usage, summary, invoices |
| Source | `operator_alerts` rows | `email_outbox` rows written with the event that caused them |
| Content rule | no customer message text, no floor prices (existing) | the same, plus nothing about any other shop |
| Opt-out | none (the founder's own switch is `ALERT_MIN_SEVERITY`) | per kind, one click, always |

Neither stream's code may look up the other's recipients. A test asserts the founder path never reads owner addresses and the client path never reads `ALERT_EMAIL_TO`.

### 2. The sender seam
A small `MailSender` interface: `send(message) -> None`, raising on failure. SMTP (`smtplib`, the existing sink's delivery code) is the first implementation. Amazon SES, Zoho,
Gmail and a local test server all speak SMTP, so the vendor is configuration. An API sender (SES API, Resend) is a second class behind the same interface if SMTP limits bite.
`SMTP_*` settings are shared by both streams; only the From address differs, and both are checked at start-up (`envcheck`, `doctor`).

### 3. Founder stream: what must reach the founder, and how urgently
Existing sources stay as they are (dead letters, queue stalls, parts down, delivery failures, subscription and payment events, console sign-in alerts, support tickets).
New: **every model failure raises an alert with a specific reason**, because the founder cannot fix what they cannot name.

`llm/failure.py` turns the provider's error text into one of these. The alert's `kind` is `llm_<reason>`, `dedup_key` is the step (`planner`, `writer`, `check`), and `business_id`
is empty (a model failure is a platform problem, so it is one alert, not one per shop). The message names the step, the reason, the provider's own words (shortened) and the fix.

| Reason | Meaning | Severity | Typical fix |
|---|---|---|---|
| `key_rejected` | 401/403 | critical | check `OPENAI_COMPAT_API_KEY` |
| `out_of_credit` | 402, quota, balance | critical | top up or raise the limit |
| `model_not_found` | 404, no endpoints | critical | check `LLM_MODEL_*` |
| `no_provider` | none configured, or every circuit open | critical | provider settings and status page |
| `rate_limited` | 429 | high | raise limit or add `LLM_FALLBACK_PROVIDER` |
| `provider_error` | 5xx, overloaded | high | usually passes; fallback provider |
| `connection` | timeout, refused | high | server network; `LLM_TIMEOUT_S` |
| `truncated` | cut off at `max_tokens` | high | `OPENAI_COMPAT_THINKING_TOKENS` or lower `LLM_EFFORT_*` |
| `bad_output` | not JSON / fails the schema | medium (rollup) | model or `LLM_JSON_MODE` |
| `unknown` | anything else | high | read the error text |

Critical means "every customer reply is failing until someone acts", so it is sent at once and repeated every 30 minutes until the alert resolves. An alert resolves itself after a
successful model call for that step (a small check in the pipeline on success), which also produces the recovery notice. The pipeline never lets alerting break a turn: the raise
is wrapped, and a failure to alert is only logged.

### 4. Client stream: what, when, how
- **Address.** Optional `business_users.email`, set in the shop app, **confirmed** by a one-time link before anything else is sent to it (so nobody can aim mail at a stranger by
  typing their address). Changing the address un-confirms it. The confirmation mail is itself a client email.
- **Kinds and defaults** (each has its own switch in the shop app; WhatsApp stays the primary channel):
  | Kind | Default | Why email |
  |---|---|---|
  | `whatsapp_disconnected` | on | WhatsApp cannot reach an owner whose number is down |
  | `daily_summary` | on | a record that does not scroll away |
  | `usage_warning` | on | plan allowance, ADR 0032 |
  | `invoice` | on | a record the owner can forward to an accountant |
  | `handoff` | off | WhatsApp is faster; email is for owners who want it |
  | `account_security` | on, no switch | email confirmed, address changed, number changed |
- **Delivery.** A row in `email_outbox` (shop, user, kind, language, subject, body, `idempotency_key`, status, attempts, next_attempt_at) is inserted in the **same transaction** as
  the event that caused it (the transactional-publishing rule of the async backbone), with a unique `(business_user_id, idempotency_key)`, so an event can never produce two emails. A
  worker sends due rows with exponential backoff, gives up after `EMAIL_MAX_ATTEMPTS`, and raises a founder alert when a shop's mail keeps failing (`client_email_failing`, medium).
  A hard bounce marks the address unconfirmed and tells the owner in the app.
- **Consent and control.** Every client email has a one-click unsubscribe link (a signed token for that user and kind, no login) and the `List-Unsubscribe` and
  `List-Unsubscribe-Post` headers. `account_security` mails are the only ones without a switch and they carry no marketing. No marketing mail exists in v1.
- **Text.** English and Hindi from the owner's language, built from the existing `notifications/messages.py` wording (one source of truth for what an alert says), as plain text plus a
  minimal HTML part. No customer message text and no prices beyond what the owner already set.

### 5. Configuration and secrets
Follows the config/secret split (ADR to follow): non-secret in `deploy/<service>/.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `ALERT_EMAIL_FROM=founder-alert@…`,
`CLIENT_EMAIL_FROM=alerts@…`, `ALERT_EMAIL_TO`, `ALERT_MIN_SEVERITY`, `EMAIL_MAX_ATTEMPTS`); secret in `secrets.env` (`SMTP_USERNAME`, `SMTP_PASSWORD`). The founder stream runs in
the web scheduler as today; the client sender runs in the same scheduler component so no new container is needed. The sending domain needs SPF, DKIM and DMARC records
for both addresses (a DNS task, listed in the go-live checklist); the domain itself is the founder's to name.

### 6. Testing
- Pure tests: failure classification (written), per-kind templates in both languages, unsubscribe token signing and tampering, header injection (a shop name with a newline).
- Database tests on the bench Postgres: outbox idempotency, retry and give-up, tenant isolation (a shop's mail is never addressed to another shop's owner, RLS), opt-out is honoured, the founder path never reads owner addresses.
- End to end locally with **Mailpit** (SMTP `1025`, inbox `http://localhost:8025`), started by `fresh-start.ps1`: trigger each kind, read it in the inbox.
- Invariants and seams untouched: no change to the pricing, reply checks or channel adapters.

## Options considered
1. **One stream with a recipient switch.** Simplest, but one mistake mails a client the founder's platform detail. Rejected: separation is the point.
2. **A hosted email product (Resend, SendGrid) with templates.** Faster to start, but vendor lock-in and a paid commitment. Rejected for now; the sender seam keeps it open.
3. **Email through the existing owner WhatsApp path only.** Does not reach an owner whose WhatsApp is the thing that broke. Rejected for `whatsapp_disconnected`.

## Consequences
- ADR 0029's sentence "not to the business owners who subscribe" now reads: the **founder stream** never goes to owners; a separate **client stream** goes only to a shop's own owner about
  their own shop. 0029 is not rewritten; this ADR supersedes only that sentence.
- New table `email_outbox`, new column `business_users.email` (+ confirmation fields and per-kind preferences), a confirmation and unsubscribe endpoint, and a small settings area in the shop app.
- A model outage now produces email within seconds; before it produced none. Expect the first week to show which reasons are common.
- Costs: SMTP provider fees (SES is a few rupees per thousand mails). No paid commitment is made by this ADR.

## How to revisit
Swap the sender class to change vendor. Change defaults per kind in one table. If client email proves unwanted, the outbox kinds can be switched off without touching the founder stream.

## Build order (each step small and reviewable)
1. LLM failure alerts (classification written; wire the three pipeline points; recovery on success). 2. Config and sender seam, `founder-alert@` / `alerts@`, Mailpit in `fresh-start`. 3. Migration:
`email_outbox`, `business_users.email` and preferences. 4. Outbox worker and templates. 5. Confirmation and unsubscribe endpoints. 6. Shop app settings. 7. Tests and docs throughout.
