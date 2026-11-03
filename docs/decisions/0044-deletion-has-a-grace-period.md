# 0044 Deleting a shop is scheduled: 60 days of grace, then the scheduler erases it

**Status:** accepted · 2026-11-03 · founder: "deletion should not be immediate, leave it for 60 days"

## Context

Deleting a shop was immediate and irreversible (ADR 0030, DPDP erasure). An owner who deletes by mistake, or in anger, lost everything at once.

## Decision

Postgres has no delayed delete, so the request is recorded and the scheduler carries it out. `DELETE /business` (owner only, shop name typed) writes a `deletion_requests` row with the date
it becomes final (`DELETION_GRACE_DAYS`, default 60, 0 to 365), pauses the assistant and stops renewals, and remembers what they were. `POST /business/deletion/cancel` puts both back exactly as they were
and removes the row. The web part's scheduler (`deletions` duty, every half minute) erases every shop whose date has passed with the same erasure as before and writes a `tenant.purged` record to the platform
audit trail (ids and counts only). Asking twice keeps the first date. While waiting, the owner can still sign in, download their data and cancel; nothing else changes for them.

The founder's console keeps its immediate erase (typed shop name), for a legal erasure request that cannot wait. Privacy copy should say "erased within 60 days of your request".

## Consequences

- Customer data is held for up to 60 days longer after a request; the privacy policy and retention policy must say so (founder to confirm the wording).
- A shop in grace still counts as a shop for backups; the purge removes it from the live database only, so the backup retention period is the real outer limit.

## How to revisit

Set `DELETION_GRACE_DAYS=0` for immediate erasure again. A different period per plan or per request is a small change in `request_deletion`.
