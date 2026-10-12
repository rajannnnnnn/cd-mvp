# Performance: what each action costs, on production-sized data

Measured 2026-10-12 with `backend/loadtest/actions.py` against `backend/loadtest/scale_data.py` data, on a 4-core, 16 GB sandbox where the
API, the database and the load generator all share the machine. Treat the absolute numbers as a floor for a dedicated server; compare actions
with each other.

## The data
50 shops, 20,000 products, 150,000 customers and conversations, **3,000,000 messages**, 450,000 turns, 30,000 deals: 1.5 GB in Postgres 16
(messages alone: 1.2 GB with indexes). That is about 60,000 messages per shop, a busy quarter, and well past the PRD's NFR-5 target of 50 shops.
Each action is sent as real HTTP requests as one shop's owner; the database side comes from `pg_stat_statements` and `pg_stat_database`.
The model is the local stand-in, so none of this includes language-model time, and the WhatsApp send/receive path is covered separately by
`loadtest/run.py`.

## Results (after the fixes below)
| Action | alone: p50 ms | busy (8 at once): req/s | busy p50 / p95 ms | overloaded (32 at once): req/s | p95 ms | DB queries / DB ms per request |
|---|---|---|---|---|---|---|
| who am I  (GET /auth/me) | 4.9 | 288.6 | 25.6 / 42.5 | 207.2 | 323.8 | 3.0 / 0.1 |
| shop settings  (GET /business) | 5.2 | 230.9 | 31.6 / 56.7 | 193.6 | 408.6 | 4.0 / 0.15 |
| products list, 100  (GET /products) | 16.8 | 80.6 | 84.4 / 206.1 | 74.1 | 565.8 | 105.0 / 4.1 |
| offers list  (GET /offers) | 5.5 | 267.2 | 25.4 / 40.2 | 212.7 | 405.9 | 3.0 / 0.06 |
| chats list, 30  (GET /conversations) | 26.3 | 122.5 | 63.1 / 85.8 | 113.9 | 366.0 | 3.0 / 20.56 |
| one chat with history  (GET /conversations/{id}) | 9.6 | 188.3 | 39.4 / 58.5 | 159.7 | 422.2 | 7.0 / 0.28 |
| home dashboard numbers  (GET /metrics/overview) | 55.0 | 68.0 | 114.6 / 153.0 | 71.1 | 630.0 | 8.0 / 69.49 |
| analytics, 30 days  (GET /analytics) | 386.1 | 9.4 | 855.0 / 990.1 | 10.1 | 3486.0 | 17.0 / 742.24 |
| billing  (GET /billing) | 11.1 | 194.8 | 39.5 / 50.3 | 174.6 | 332.1 | 9.0 / 1.61 |
| support tickets list  (GET /support/tickets) | 5.5 | 289.2 | 26.4 / 36.9 | 211.8 | 358.0 | 3.0 / 0.04 |
| price ladder preview  (POST /pricing/ladder) | 4.3 | 324.6 | 22.6 / 37.3 | 224.2 | 431.2 | 1.0 / 0.02 |
| add a product  (POST /products) | 12.2 | 145.6 | 52.7 / 72.6 | 139.3 | 307.2 | 16.0 / 1.16 |
| switch a product off  (PATCH /products/{id}) | 9.3 | 213.8 | 36.3 / 48.0 | 171.8 | 409.0 | 8.0 / 0.49 |
| delete a product  (DELETE /products/{id}) | 7.6 | 268.8 | 26.0 / 32.6 | 187.8 | 402.3 | 10.0 / 0.4 |
| add an offer  (POST /offers) | 9.0 | 232.1 | 33.6 / 41.7 | 164.9 | 485.4 | 7.0 / 0.35 |
| delete an offer  (DELETE /offers/{id}) | 6.7 | 268.2 | 29.2 / 37.3 | 205.8 | 346.9 | 5.0 / 0.2 |

How to read it: *alone* is one person using the app; *busy* is eight actions at once; *overloaded* is thirty-two at once, where the
numbers show queueing in the single API process, not database trouble. Database time per request is below 4 ms for everything except the
chats list (20 ms), the dashboard (about 70 ms) and analytics (about 750 ms).

## What the test found, and what changed
| Finding | Before | After | Fix |
|---|---|---|---|
| Dashboard and analytics compared a computed date with each day of the range, so a shop's whole message history was read about 30 times per figure | dashboard 2,579 ms, analytics 2,791 ms | dashboard 55 ms alone (115 ms busy), analytics 386 ms alone | one range scan grouped by day (`daily_series`), an index on `messages (business_id, created_at)` (migration 0009) |
| 12 foreign keys had no index (for example `handoffs` and `deals` by conversation, `business_users` by account, which the sign-in reads) | table scans on lookups, deletes and updates of the parent | indexed | migration 0010 and `tests/test_schema_indexes.py`, which fails whenever a foreign key is left unindexed |
| Saving an offer read every offer back to find the one just saved | 95 req/s, 206 rows read per save, p95 192 ms | 232 req/s, 7 rows, p95 42 ms | `repo.get_offer` |

## Where the limits are
* **The API process, not the database.** One API process serves roughly 200-300 simple requests per second; past that, latency is queueing.
  Run several API processes behind the web server (the compose file runs one) and the database has plenty of room: it spent 0.1-4 ms per request.
* **Analytics** is the heaviest read: 0.4 s alone, about 1 s with eight at once, on a shop with 60,000 messages. It is a once-in-a-while page, so
  this is acceptable for the pilot. The proper fix is a daily roll-up table (step 3 of ADR 0021) so it never reads raw messages.
* **Chats list** costs 20 ms of database time (a latest-message lookup per row); fine now, worth a look beyond about 100,000 conversations per shop.
* **Products list** shows 105 statements for 100 products. They are the per-variant check of whether a lowest price is set (a function called inside
  one query), not 105 round trips; total database time is 3 ms.
* **Writes** are cheap: adding a product runs 16 statements in about 1 ms of database time.
* Not yet measured: the full customer-message-to-reply path at this size (`python -m loadtest.run conversations`), sustained hours-long load, and
  a database on a separate machine (network time).

## Re-running it
```
python -m salesai migrate                                   # on the target database
python -m loadtest.scale_data --admin-url postgresql://postgres@HOST/salesai_scale       # scratch database only
python -m loadtest.actions --base http://HOST:8000 --db-url postgresql://postgres@HOST/salesai_scale --owner +917000000001 --requests 300 --concurrency 8
```
Run the API as `python -m salesai api` (no workers) so background polling does not add to the counters. `pg_stat_statements` must be loaded
(`shared_preload_libraries`); the script installs the extension into a `monitoring` schema, never `public` (the migration tool revokes on public functions and would stop).
