# 0008 Number-centric authentication

**Status:** accepted · 2026-10-04

## Context
Owners run their business on WhatsApp numbers; low-literacy owners rely on WhatsApp. The founder asked for an
authentication that is "perfect and number-centric".

## Options
Email + password · social login · SMS OTP · **one-time code delivered over WhatsApp to the account's own number**.

## Decision
The WhatsApp number is the account identity (`accounts.phone`, E.164). Sign-in: request a code (delivered through the
platform's WhatsApp sender, an authentication template; in dev the simulated phone shows it) → verify → short-lived
access JWT (15 min) + rotating refresh token.
- Codes are 6 digits, stored only as a keyed hash (HMAC with `OTP_SECRET`), expire in 5 min, max 5 attempts; a wrong
  attempt is counted even though the request fails.
- Enumeration-safe: unknown or blocked numbers get the same response and no message.
- Rate limits per phone, per IP and a minimum interval between requests.
- Refresh tokens rotate on every use; **reuse of an old token revokes the whole family** (theft detection). Sessions are
  server-side rows, so logout and device revocation take effect immediately, not at token expiry.
- Roles: owner, staff (chats and stock only), operator (platform). An account may belong to several businesses
  (a ticket step lets the user choose). Operator "open as owner" issues a 30-minute impersonation token, audited.

## Consequences
No passwords to leak or reset. Login depends on WhatsApp delivery to the owner's number; a template and the platform
number are required in production (`OTP_CHANNEL=whatsapp_cloud`). Tested in `test_auth.py` (replay, reuse detection,
rate limits, revocation, impersonation, enumeration).

## How to revisit
Add a second factor or a passkey on top; the session model does not change.
