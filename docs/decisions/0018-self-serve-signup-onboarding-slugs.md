# 0018 Self-serve sign-up, onboarding and shop addresses

**Status:** accepted · 2026-10-04 · extends ADR 0008 (number-centric authentication) on the founder's instruction

## Context
The product so far assumed an operator created every business. The founder wants a full self-serve SaaS: a visitor from the
marketing site or an advertisement signs up with a mobile number, sets up a shop, connects WhatsApp and lands on a dashboard
at an address that carries the shop's name. One-time codes are not available yet (no legal entity to hold the WhatsApp sender),
so for now any code should be accepted.

## Decisions
1. **Sign-up is sign-in.** Any valid mobile number can request a code; verifying it creates the account when the number is new.
   A session for an account with no business has the role `setup`, which can call only the onboarding endpoints. The
   anti-enumeration behaviour of ADR 0008 is dropped because a number's existence is no longer a secret.
2. **`OTP_ACCEPT_ANY` (demo only).** When true, any non-empty code signs a number in. The application refuses to start with it
   in `ENV=production`; the Docker demo sets it. The risk is stated plainly: with it on, anyone can sign in as any number, so it must
   never face real customers' data. Turning it off is one setting; nothing else changes.
3. **Onboarding steps** (business, shop details, products, WhatsApp, assistant preferences, go live) reuse the normal APIs.
   Progress is computed from real data (a product exists, a number is connected), not from stored flags, except for "skipped" and
   "assistant reviewed". Going live needs at least one product. A shop that goes live without a number starts answering when the
   first number connects.
4. **Shop slug.** `businesses.slug` is unique, 3-40 characters, generated from the name, editable, and never one of the platform's
   own path segments (the list is in code and mirrored in the frontend router). The app lives at `/app/<slug>/…`; `/<slug>` is a
   short link that redirects there (reverse proxy and dev server). The router's base path is fixed per page load, so changing shop is a
   full navigation, and old `/app/chats` style links are moved to the signed-in shop's address.
5. **Attribution.** The marketing site stores the campaign (`utm_source/utm_campaign` or `src`) and sign-up records it on the account.

## Consequences
- Migration 0005 backfills slugs for existing businesses. It must lift row-level security for that one statement, which a test
  (`test_migrations.py`) now guards by migrating a database that already holds data.
- With codes disabled, the OTP channel and platform sender can be configured later without code changes.

## How to revisit
Set `OTP_ACCEPT_ANY=false` and `OTP_CHANNEL=whatsapp_cloud` once the platform number exists. To change URL shape, only `lib/slug.ts`,
the Caddyfile vanity rule and the backend reserved list move together.
