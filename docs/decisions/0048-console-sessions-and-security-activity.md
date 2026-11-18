# 0048 — Console sessions and security activity: see every sign-in, end any session, and ending one needs the authenticator

Status: accepted (2026-11-09). Builds on ADR 0047 (founder sign-in) and ADR 0028 (the console).

## Context
The whole company's data is behind the founder's console. If someone gets in, the founder must be able to see exactly who: when, from where, on what
device, for how long and how they got past the second layer, and must be able to cut them off. Cutting someone off has to be as hard as signing in: an
attacker inside a live session must not be able to sign the founder out, hide their own session or lock the founder out of the screen that shows them.

## Decision
**Every session records how it started.** The console stores, for each session: the address and its /24 (/48) network, the country when the front proxy
supplies one, the browser and system (parsed from the User-Agent into words like "Chrome 142 on Windows 11"; the raw text is kept, truncated), a short
browser id (the first 16 hex characters of the trusted-browser hash; never the token), how the sign-in was proven ("authenticator" or "authenticator +
WhatsApp code", and why the second layer was asked for), when it started, when it was last used (updated at most once a minute), when and why it ended
(signed out, signed out by the founder from another session, extended, expired, enrolment replaced) and its expiry. Duration is derived.

**"Where" is the address and, optionally, the country from a header the front proxy sets** (`ADMIN_GEO_HEADER`, for example `CF-IPCountry`). There is no IP-to-location
lookup: a lookup service would be sent every founder sign-in address, and a bundled database needs a licence account. The address, its network, and whether it is
a network the founder has signed in from before are shown instead.

**Failed attempts are recorded too**, on the platform audit trail with the same address, device and step, because the attacker's wrong codes are often
the first sign of an attack. The console shows sessions and attempts together.

**Ending a session needs a fresh authenticator code.** `POST /admin/v1/auth/sessions/{id}/revoke` and `.../revoke-others` take an authenticator code, which
is checked like a sign-in (works once, counts wrong codes toward the same lock). Ending your *own* session (sign out) needs nothing, since it cannot hurt.
Ending a session can also forget that browser, so its cookie no longer skips the WhatsApp code; "sign out everywhere" also forgets every trusted browser.
A session cannot extend or revoke by itself with only its token: a stolen token alone cannot lock the founder out or tidy up its own traces.

**The founder is told as it happens.** A sign-in that needed the second layer for a new browser or network (the cases that matter) raises a High alert
(`admin_new_sign_in`) with the address and device, so it reaches the founder by email or WhatsApp even when they are not looking at the console.

## Consequences
- Migration 0035 adds columns to `admin_sessions` (console-only). No new table.
- The console gains a Security screen: active sessions with an End button (asks for the authenticator code), recent sign-ins and refused attempts, and "End all other sessions".
- Without a location database, "where" is an address plus an optional country; a deployment behind Cloudflare gets the country for free by setting one variable.
- A founder who loses their authenticator cannot end sessions; the recovery is the server command (ADR 0047), which ends every session.

## How to revisit
If a real location is wanted: add a licensed offline database behind `ADMIN_GEO_HEADER`'s code path (`modules/auth/device.py::where_label`) so that nothing is sent out.
