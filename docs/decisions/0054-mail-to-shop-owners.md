# 0054 Mail to shop owners: one component, a database outbox, a sender per purpose

**Status:** accepted · 2026-11-24 · founder: payment confirmations and support mail, from proper addresses (support@, billing@); founder mail keeps top priority; every template in one place; nothing may block

## Context
Only the founder's own alerts were mailed (ADR 0029, 0052). Shop owners need payment receipts and support notices by email, from several proper addresses. The app stored no email address for a shop (accounts are phone-only), and support is an in-app thread by design.

## Decision
1. **One mail module, two delivery paths.** Founder alerts keep their direct path (the alert router): it must work when the queue or database is in trouble, and a burst of owner mail must never delay it. Owner mail uses a `mail_outbox` table.
2. **A database outbox, not a new queue or service.** The row is written in the same transaction as the event (the invoice becoming paid, the ticket reply), so the mail cannot be lost and cannot hold the event up. The web scheduler sends it right after the founder's alerts, a few per round under `CLIENT_EMAIL_PER_MINUTE` (a share of SES's rate, so alerts always have room), with retries and backoff, and one mail per `dedupe_key`. A crashed sender loses nothing (a 5-minute lease).
3. **A sender per purpose** (`billing`, `support`, `account`, `updates`) from env (`CLIENT_EMAIL_FROM_<PURPOSE>`, falling back to `CLIENT_EMAIL_FROM`), `Reply-To` the same address, and an optional hidden copy (`CLIENT_EMAIL_BCC_<PURPOSE>`) to a real mailbox, because mail sent through SES never reaches a Google Sent folder.
4. **One optional address per shop** (`businesses.billing_email`), asked in Settings. Without it nothing is sent and the row is marked skipped; the in-app and WhatsApp notices stay.
5. **All templates in `modules/mail/templates.py`** with one shared design (`layout.py`); `python -m salesai mail-preview` writes them all to a folder. Founder mail gets the same design (its text is unchanged).
6. **Privacy:** the outbox stores no address (read at send time) and no ticket text (mails link to the app); the log and the console never show an address or body.
7. **First mails:** payment confirmed, payment failed (when a gateway refuses a charge), trial ending (3 days before), request received, request replied, deletion scheduled.

## Not done
- Inbound mail is not read: a reply to `support@` lands in the real mailbox (via Reply-To and the copy) and is answered by hand, not attached to the ticket.
- No unsubscribe or marketing mail (all mails here are transactional).
- SES bounces and complaints are not fed back (SES suppression list in the AWS console is the place for now).
- Mail templates are English only; Hindi next to each by name when added.

## How to revisit
Empty `CLIENT_EMAIL_FROM*` turns client mail off (rows are skipped). A real queue can replace the outbox without touching the templates: `enqueue` is the only producer.
