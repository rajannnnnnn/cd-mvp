# 0030 Auditing, retention and the DPDP posture

**Status:** accepted · 2026-11-02

## Context

The product holds other people's private commercial conversations. Three obligations meet here: the
Technical Design's auditability property (pricing and settings changes with actor and time; every AI turn
with decision, model, prompt version, tokens, cost and latency), CR-10 and NFR-10, and India's DPDP regime.

The DPDP Rules were notified on 13 November 2025. The core obligations — consent and notice, security
safeguards, breach reporting within 72 hours, retention and erasure, data-principal rights and
cross-border conditions — take effect **13 May 2027**. The Consent Manager registration dated
13 November 2026 applies to consent managers, which we are not. (Secondary sources; confirm against the
Gazette before relying on the dates.)

`audit_log` exists with business, actor, action, entity, detail and time, and is written from catalog,
sales, auth, handoffs, notifications, tenants, onboarding and operator paths. Per-tenant export and hard
delete exist. What is missing is tamper resistance, a written retention schedule, and the documents that
a processor is expected to have.

## Options

For tamper resistance: do nothing; revoke write access at the database; or a hash chain over rows.
For retention: ad-hoc cleanup as today, or a declared per-type schedule enforced by the scheduler.

## Decision

**The audit log becomes append-only at the database.** UPDATE and DELETE are revoked from every
application role; only the migration role may alter the table. This is a grant change, it is cheap, and it
means no application bug and no compromised component can quietly rewrite history. A tamper-evident hash
chain is recorded here as a deliberate deferral — it defends against someone who already holds the
migration credentials, which is not the v1 threat model.

**Audit coverage is required**, not best-effort, on: pricing and settings changes; every admin action on
the founder plane; every break-glass read of a conversation (ADR 0028); consent and opt-out changes;
tenant export and delete; AI on/off switching; and every learned fact accepted or rejected (ADR 0031). A
test asserts coverage so a new write path cannot silently skip it.

**Retention is a declared schedule per data type**, configurable and enforced by the existing scheduler:
raw webhook payloads (`RAW_WEBHOOK_RETENTION_DAYS`, already in place), messages, turn records, audit log,
support attachments, alert history. Each type has a documented reason for its period. Deletion is tested
on a database with data, since the migration bug found in session 4 showed that row-level security can
hide exactly the rows a maintenance job is meant to touch.

**The DPDP posture is written down, because the roles matter.** Each shop is the Data Fiduciary for its
own customers; we are its Data Processor. That means the legal pages need a **Data Processing Agreement**,
not only a privacy policy, and that breach obligations run through us to the shop and onward. A breach
runbook with the 72-hour clock, named roles and a notification template is written before pilot rather
than during one.

**Build to the May 2027 obligations now**, while they are cheap: export and erasure exist, retention
becomes explicit here, consent and opt-out are already recorded, and cross-border posture follows the
hosting decision (an Indian region is the starting point in the Technical Design).

**Housekeeping:** two ADRs share the number 0020 (`channel-gateway` and `support-tickets`). Published
files are not renumbered; `docs/decisions/README.md` notes the collision. **0024 is reserved** for the
client-rendered-site ADR that currently exists only on the founder's machine.

## Consequences

- History cannot be rewritten by the application, which is what makes the audit log worth having.
- Append-only means corrections are new rows, and any code that expected to amend an audit row breaks
  loudly at deploy time rather than quietly in production.
- A declared retention schedule shrinks the database and the breach blast radius, and it is a question
  every serious customer and every auditor asks.
- The DPA and breach runbook are documents, not code, and they need the legal entity decided — which is
  already an open founder decision.

## How to revisit

If a pilot customer or an auditor requires tamper evidence, add the hash chain; the table shape already
supports it. If the DPDP timeline moves — the ministry has proposed shortening one window already — the
schedule is configuration and the documents are text.
