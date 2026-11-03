# 0045 Stand-ins for outside services live outside the product and are wired by settings

**Status:** accepted · 2026-11-03 · founder: "all mocks separate and stay away, only connect on variables configuration"

## Context
The founder cannot get real Meta credentials until the business's partnership deed exists, yet needs to run the whole product, including sign-in codes arriving on a phone and the shop owner connecting a number.
The product had an in-process simulator, which is mock code inside the product.

## Decision
- The product's deployed code speaks only the real protocols. Every address and credential it needs is a setting whose default is the real service (`META_GRAPH_BASE`, `META_SDK_URL`, `META_SIGNUP_ORIGINS`, `META_APP_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, the platform sender id and token).
- A stand-in is a separate program in `tools/` with its own container, importing nothing from the product and not imported by it: `tools/meta-sim` plays Meta (Graph API, signed webhooks, template store, sign-up script and popup, 24-hour rule, errors) and the phones
  (any number, chats with each shop and the platform sender, sign-in codes arriving as WhatsApp messages, voice notes and photos) with a per-phone audit.
- It is switched on by changing settings and off by changing them back. It was checked end to end against the unmodified product (sign-in code on a phone, sign-up, number connect, customer chat and reply) and by a contract test that runs the product's real Meta code against it.

## Consequences
- Real credentials later mean editing `.env` files only.
- The product still contains its older in-process simulator (used by its own test suite and by the one-machine demo stack), off by default in every deployed part. Retiring it means moving those tests onto `tools/meta-sim`; it is recorded as follow-up work, not done.
- What the stand-in cannot show: Meta's real verification, template approvals, rate limits and payload quirks.

## How to revisit
When the business has real Meta access, run the same journey against a real test number and compare the wire log with Meta's behaviour.
