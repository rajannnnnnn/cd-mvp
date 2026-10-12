# 0021 Grouping the tables into domains (plan, not yet done)

**Status:** proposed · 2026-10-12 · founder wish: keep conversation-type tables apart from operations-type tables (who subscribed, when it ends, WhatsApp accounts)

## Context
37 tables sit in one flat `public` schema. They behave very differently: a handful grow without bound (messages, turns), most stay small
(subscriptions, numbers), and they have different owners, retention and backup needs. Row-level security, the four database roles and the
module boundaries (`scripts/check_boundaries.py`) already separate them in code; the database does not yet show it.

## The domains
| Domain (schema) | Tables | Size / behaviour |
|---|---|---|
| **platform** (operations, the control plane) | accounts, businesses, business_users, business_keys, whatsapp_numbers (the WABA side), subscriptions, invoices, payments, auth_sessions, otp_challenges, support_tickets, support_messages, operator_alerts, audit_log | small, rows live for years, must never be lost, read by the operator console |
| **shop** (what a shop sells and how) | products, product_variants, pricing_policies, price_floors, offers, style_examples | thousands of rows per shop, edited by hand; `price_floors` stays behind its own role (INV-1) |
| **convo** (the conversations, high volume) | customers, conversations, messages, turns, negotiations, deals, handoffs, knowledge_gaps, owner_messages, config_proposals | grows with usage: about 60,000 messages per busy shop per quarter; the only tables that need partitioning or archiving |
| **infra** (plumbing) | webhook_events, outbox, jobs, rate_buckets, idempotency_keys, sim_messages, schema_migrations | short-lived rows, purged on a schedule |

## Steps, in order, each reversible on its own
1. **Schemas inside the one database** (cheap, do first). `ALTER TABLE … SET SCHEMA` per table, `search_path` set per role, grants and
   row-level security move with the table; the boundary checker gets a rule "a module touches only its own schema's tables" so the
   grouping is enforced, not just drawn. No application behaviour changes. One migration, tested by the existing 280+ tests.
2. **Retention and partitioning for `convo.messages`** when it passes about 50 million rows (roughly 800 busy shops for a year): monthly
   range partitions by `created_at`, old partitions archived to cheaper storage. The `(business_id, created_at)` index already serves
   the dashboard reads; partitioning keeps the indexes small. Measured today: 3 million messages is 1.2 GB with indexes and all reads under
   150 ms except the analytics page (see `docs/PERFORMANCE.md`).
3. **Daily roll-ups** (`convo.daily_stats`: per shop per day counts) so analytics and the dashboard stop reading raw messages at all.
4. **Separate databases** only if one database stops being enough: `platform` + `shop` on one small, strongly backed-up instance and
   `convo` on its own (or sharded by business). Composite foreign keys `(business_id, …)` to `businesses` would become plain columns,
   and tenant deletion (`modules/tenants/lifecycle.py`) already deletes by explicit list, so it can span databases.

## Not decided here
Whether to do step 1 now or after the pilot. It is safe now (a rename, with no data copy) but it touches every query's table name through
`search_path` and every migration after it, so it is cheaper before more tables are added. Recommendation: do step 1 before the pilot.
