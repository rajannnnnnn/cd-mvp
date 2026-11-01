# 0023 Pictures on support tickets

**Status:** accepted · 2026-11-02 · founder request ("add optional picture adding in the support")

## Context
Shop owners want to send a screenshot or photo with a support ticket. v1 has no object store, and the product already depends on one
external Postgres. Pictures are small, rare and private to the shop that sent them.

## Options
1. **Object storage (S3 or similar).** The right home for files in the long run, but a new paid vendor, new credentials and a new failure mode for a rarely used feature.
2. **Pictures in the database (bytea), behind one table.** No new vendor; row-level security and backups come for free; fine for a few small images per ticket.
3. **Link to a picture hosted elsewhere.** Not private, not controllable.

## Decision
Option 2: table `support_attachments` (migration 0013) with row-level security like every other shop table.
- **Accepted:** PNG, JPEG, WebP only, checked from the file's first bytes (the browser's claim is ignored); 3 MB each; 3 per message; only on the shop's own messages.
- **Served** only to a signed-in member of that shop and to platform support staff, from an authenticated endpoint (never a public address), with
  `X-Content-Type-Options: nosniff`, `Content-Disposition: inline`, `Content-Security-Policy: default-src 'none'; sandbox` and `private` caching.
- **Sent in a second step:** the message is created first, then each picture is uploaded to it, so the existing JSON endpoints did not change.
- Support staff can read pictures; sending pictures from the support side is not part of this change.

## Consequences
Database size grows with pictures (at most 9 MB per message). Deleting a ticket or shop deletes its pictures (foreign keys). Pictures are not scanned for malware or
inappropriate content; they are only ever shown as images inside the app.

## How to revisit
Move the bytes to object storage by changing only where `data` is written and read (`add_picture`, `my_picture`, `picture_for_support` in `api/routes/support.py`);
the table keeps the metadata and the access rules. Raise the limits in `MAX_PICTURE` / `MAX_PICTURES` and the matching `CHECK` in the migration.
