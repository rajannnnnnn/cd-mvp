# 0020 Support tickets from Settings

**Status:** accepted · 2026-10-11 · founder request: a Support tab in Settings that opens a ticket immediately

## Context
Owners need a way to ask for help without leaving the product, and the platform team needs to see and answer it. Until now the
operator console deliberately returned no tenant content (counts and health only).

## Decision
- **A ticket exists the moment it is sent**: one form (kind, title, details), one request, no confirmation or email step. Support is
  told at once through an `operator_alerts` row (`kind='support_ticket'`), which already feeds the alert routing of ADR 0017.
- **Tables** `support_tickets` / `support_messages` (migration 0008) are tenant tables under row-level security, so a shop sees only
  its own. Statuses: `open` (waiting for support), `answered` (waiting for the owner), `resolved`; a new message from the shop reopens.
- **Operators read ticket text** through `/operator/support*` (list, thread, reply, set status). This is the one place the console
  shows text from a shop, because the owner wrote it for support. Conversations, customers and prices stay out of reach as before.
- **Abuse limit:** 20 new tickets per shop per day. Tickets are included in the owner's data export and are erased with the shop.
- Owners and staff can both open tickets; only platform operators can answer.

## Consequences
No email or WhatsApp notification to the owner when support replies yet (the thread refreshes while the Support tab is open).
That, attachments and an SLA clock are the natural next steps; revisit when there is a support team.
