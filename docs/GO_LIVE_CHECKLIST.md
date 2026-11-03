# Go-live checklist

What remains once the code is complete: things that need real keys, real accounts or business facts, and checks that
can only be made against the real services. Built up as the work goes; each item says why it matters.

## Keys and configuration (environment)

- [ ] **Build and start each part with Docker** (`scripts/stack.sh all`, `deploy/README.md`). The compose files are validated
      (`docker compose config`) and tested against the application's production rules, but no image has ever been *built* or
      *started* in the development environment (no Docker daemon). Confirm each part's `/healthz` and that the site, `receive`,
      `send` and `agent` come up healthy.
- [ ] **Each part's `.env`** from its `.env.example` (`deploy/db|web|gateway|agent`). `MASTER_KEY` must be the same in web and
      gateway; the four database URLs the same everywhere. The web part must hold no Meta secret and the agent no WhatsApp token.

- [ ] **LLM:** `LLM_PROVIDER=openai_compat`, `OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1`,
      `OPENAI_COMPAT_API_KEY` as a secret; allow `api.groq.com` in network access. (ADR 0034)
- [ ] **Meta:** `META_APP_SECRET`, `META_VERIFY_TOKEN`, platform sender id and token; `MASTER_KEY`, `JWT_SECRET`,
      `OTP_SECRET` — real values, not placeholders (startup refuses placeholders in production).
- [ ] **Meta prices:** confirm on Meta's pricing page that service messages are billed after 1,000 free per number per
      month, and set `META_RATES_INR` / `META_FREE_SERVICE_PER_NUMBER` accordingly. (ADR 0026)

## Meta (needs a real app and test number)

- [ ] **Subscribe the app's webhook to every field we read:** `messages`, `smb_message_echoes`, `account_update`,
      `phone_number_quality_update`, `account_review_update`, `account_offboarded`, `account_reconnected`.
      A field not subscribed is never delivered, whatever the code handles.
- [ ] **Record a real payload for each field** and replace the synthetic fixtures. In particular confirm the shape of
      `account_offboarded` and `account_reconnected` (added by Meta in April 2026): the code accepts them both as fields
      of their own and as events inside `account_update`, because their documentation could not be read when they were
      built. Watch the `salesai_webhook_unhandled_total` metric for fields Meta sends that we ignore.
- [ ] **Account events in the real shape:** check that a disconnect naming only the WhatsApp Business Account reaches
      the right shop (the router falls back from phone-number id to display phone to business account).
- [ ] **Embedded Signup v4 with coexistence**, end to end, from Settings → Numbers; pin `graph_version`.
- [ ] **Alert email set up (you only; never shops).** Pick any SMTP provider, verify a sender address on your own domain (SPF/DKIM so alerts do not land in spam), then set `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`, `SMTP_HOST`, `SMTP_USERNAME` and `SMTP_PASSWORD` in `deploy/web/.env` (the password as an environment secret, never in chat). Send yourself a test: raise a High alert in the operator console and confirm it arrives within a minute, then resolve it and confirm the recovery email.
- [ ] **Message templates approved** for owner alerts, the disconnect alert and sign-in codes.
- [ ] **Tech Provider approval** — gates self-serve onboarding for real shops.

## AI (needs the LLM key)

- [ ] Run the evaluation suite on gpt-oss-20b and fix any failures; check the truncation count (a reasoning model
      spends part of `max_tokens` thinking). `python scripts/benchmark_models.py run --yes`
- [ ] Measure real cost per conversation and set the package caps from it. (ADR 0032)

## Business facts (from the founder)

- [ ] **Data retention policy** (DPDP, core obligations from 13 May 2027): how long to keep customers' chats and AI
      records after they are no longer needed; set `CHAT_RETENTION_DAYS` / `TURN_RETENTION_DAYS` (0 keeps them while
      the shop is active). Publish the same periods in the privacy policy. (ADR 0030)

- [ ] Legal entity, address, GSTIN; invoice seller fields; the bracketed placeholders in the legal pages.
