# 0019 Billing: plans, trial, GST invoices, payment provider port

**Status:** accepted · 2026-10-04 · brings payments/billing, previously out of scope, into the build on the founder's instruction

## Context
A SaaS needs plans, a trial, invoices and a way to take money. The founder has no legal entity or payment-gateway account yet.
Prices are not decided (the PRD says "after cost measurement").

## Decisions
- **Catalogue in code** (`modules/billing/plans.py`): Starter, Growth, Scale and a hidden Pilot plan, in paise, with included
  AI conversations, an overage rate and limits (numbers, products, team). **The numbers are placeholders** pending the founder. One
  script exports the catalogue to the frontend and a test fails when the copies differ, so the pricing page, the billing screen and
  invoices cannot disagree.
- **Trial:** 14 days of Growth on sign-up, no card. When it ends the assistant pauses (reversibly; data untouched) and paying
  switches it back on. Shops created by hand are `pilot`: no charge, no limits.
- **Subscriptions** (`trialing → active → past_due → canceled`) with monthly or yearly (two months free) billing. Upgrades bill
  immediately with a credit for unused time; downgrades and cancellations apply at the end of the paid period. A renewal invoice adds
  overage for the period just ended. An unpaid invoice makes the subscription `past_due` after three days and pauses the assistant
  after a further seven (grace). All of it runs from the leader-elected scheduler and is idempotent.
- **Invoices** carry 18% GST, a sequential number and a printable HTML tax invoice; the seller name, address and GSTIN are
  configuration placeholders until the entity exists.
- **Payment provider port.** `test` settles instantly and is labelled TEST MODE in the UI; `manual` (bank transfer/UPI) keeps the
  invoice open until the platform team confirms it in the operator console. The test gateway is refused in production, so production
  can charge only through the manual provider until a gateway adapter (Razorpay is the natural choice) is written.
- **Usage** is the number of conversations the assistant replied in during the period. Limits for products, numbers and team are
  enforced when adding; conversation overage is billed, never cut off. Analytics and reports are Growth-plan features.
- Billing tables are written only by the platform role; tenants can read their own rows (row-level security, tested).

## Consequences
No real money moves in this build. Card/UPI collection, refunds, dunning emails/WhatsApp and tax filing are out of scope.

## How to revisit
Edit `plans.py`, run `python scripts/export_plans.py`. Add a gateway by implementing `PaymentProvider.charge` and a webhook that
calls the same settle function.
