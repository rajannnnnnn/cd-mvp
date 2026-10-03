# High-Level Design (HLD)

This HLD describes the shape the system must have: separate frontend and backend, asynchronous processing, database-enforced tenancy, swappable vendors, and a path from one machine to many. Named products in it (Postgres, Redis Streams, Caddy, MinIO, specific queue and process names) are illustrations of a role, not decisions. The coding agent chooses implementations using the decision process and invariants in the Technical Design; the properties and principles here are what must hold. The modular-monolith structure is likewise the recommended starting position, to be revisited by ADR if measurements justify splitting services.

> The original document contained two diagrams (system context and inbound message lifecycle). They are not reproduced here; the tables and text below carry the same information.

## Architecture principles

The system is a modular monolith with separate process roles: one backend codebase, strict module boundaries, and several entrypoints (API, webhook ingress, workers) that run together on one machine or separately at scale. The counterpart approach, microservices from day one, isolates more but adds network calls, deployment overhead and distributed debugging before there is traffic to justify them. Module boundaries here are drawn so any module can become its own service later without rewriting its logic.

1. **Ports and adapters (hexagonal architecture).** Domain logic depends only on interfaces it defines: MessagingChannel, LLMProvider, Transcriber, Queue, BlobStore, PaymentProvider. WhatsApp Cloud API, a specific LLM vendor, Postgres-backed queues and S3 are adapters behind them. Swapping a vendor touches one adapter. The counterpart, a layered architecture, lets infrastructure leak upward into business logic.
2. **Asynchronous by default.** Anything slow or failure-prone (LLM calls, transcription, sending to Meta) runs in workers fed by queues. HTTP handlers only validate, persist and enqueue.
3. **Events between modules, not direct calls.** Modules publish domain events (`message.received`, `turn.decided`, `handoff.created`) through a transactional outbox; consumers subscribe. A module can be added, removed or moved to another machine without changing producers.
4. **Idempotent everything.** Every consumer can process the same event twice safely, keyed by Meta message ID, event ID or job ID. Retries are therefore always safe.
5. **Stateless processes.** No process holds conversation state in memory between jobs; state lives in Postgres. Any instance can handle any job, so scaling is adding instances.
6. **The database enforces invariants.** Tenant isolation (row-level security, composite foreign keys), uniqueness and pricing constraints live in the schema, not only in code.
7. **Deterministic code owns money and rules; the LLM owns language and judgment.** Prices, floors, the 24-hour window and opt-outs are enforced in code. The LLM chooses actions and writes text, and its output is validated before sending.
8. **Configuration over code.** Behavior that varies by tenant (sales style, timing priors, handoff triggers) is data. Behavior that varies by deployment (queue backend, provider, process role) is environment configuration.
9. **Frontend and backend are separate deployables** that talk only over the versioned REST API.

## System context and components

One backend image runs every backend box below under a different ROLE; the frontend is a separate static build.

Requests only touch the API or ingress, which persist and enqueue; every LLM call, transcription and send happens in a worker, so slow providers never block Meta's webhooks or the dashboard. All processes share state only through Postgres, the queue and blob storage.

| Component | Responsibility | Scales by |
|---|---|---|
| Frontend | Owner dashboard and operator console | CDN or static host |
| REST API | Authenticated owner and operator operations | Instances behind a load balancer |
| Webhook ingress | Signature check, raw event store, enqueue | Instances on request rate |
| Event router | Meta payloads to domain events, tenant resolution | Workers on `inbound.events` depth |
| Turn worker | State machine, end-of-turn, agent pipeline, pricing engine | Workers on `conversation.turns` depth |
| Media and alerts | Transcription, owner alerts, daily summaries | Workers on their queue depth |
| Outbound sender | Read receipts, typing, sends, Meta rate limits | Workers, partitioned by number |
| Scheduler and outbox relay | Release delayed jobs; publish outbox rows | One active instance each |

## Deployment topologies

The same container images run in every topology; only environment variables and the number of instances change.

| Component | Topology A: single machine (v1) | Topology B: split (scale-out) |
|---|---|---|
| Reverse proxy and TLS | Caddy in Docker Compose | Cloud load balancer |
| Frontend (static build) | Served by Caddy from the same machine | Static host or CDN on its own domain |
| API process (`ROLE=api`) | 1 container | N instances behind the load balancer |
| Webhook ingress (`ROLE=ingress`) | Same container as API | Separate small instances, scaled on request rate |
| Workers (`ROLE=worker`, `WORKER_QUEUES=…`) | 1 container consuming all queues | Separate pools per queue, autoscaled on queue depth |
| Scheduler (`ROLE=scheduler`) | 1 container | 1 active instance (leader-elected), 1 standby |
| Postgres | Container with a persistent volume and nightly backups | Managed Postgres with point-in-time recovery and read replicas |
| Queue backend (`QUEUE_BACKEND`) | `postgres` (tables + row locking) | `redis` (Redis Streams) or a managed queue |
| Blob storage (`BLOB_BACKEND`) | MinIO container (S3-compatible) | S3-compatible object storage |

### Rules that keep both topologies possible

- The frontend reads the API base URL from runtime configuration, never a hard-coded host.
- The API sets CORS from `ALLOWED_ORIGINS`, so a frontend on another domain works without code changes.
- Processes find each other only through Postgres, the queue backend and blob storage; no process calls another process's internal address.
- Every process exposes `/healthz` (alive) and `/readyz` (dependencies reachable) for any orchestrator.
- Migrations run as a separate one-off job, never at process startup, so many instances can start at once.

## Inbound message lifecycle

Every customer message follows one path from Meta's webhook to a scheduled reply, and any newer message restarts it.

Each step is a separate job, so a failure retries only that step. The end-of-turn check loops as delayed jobs until the customer looks done or the maximum wait passes; the agent pipeline asks the pricing engine for any number before the writer phrases it; the outbound sender re-checks the 24-hour window and AI pause just before each send.

## Queuing and async processing

All work after the webhook acknowledgment flows through named queues behind one Queue interface; v1 implements it on Postgres, and scale-out swaps in Redis Streams or a managed queue without changing producers or consumers.

| Queue | Producer | Consumer | Ordering key | Notes |
|---|---|---|---|---|
| `inbound.events` | Webhook ingress | Event router | None | Raw Meta events normalized into domain events |
| `conversation.turns` | Event router, scheduler | Conversation worker | `conversation_id` | Serialized per conversation; carries `conversation_version` |
| `outbound.actions` | Conversation worker, notifications | Outbound sender | `whatsapp_number_id` | Mark read, typing, send; rate-limited per number |
| `media.transcribe` | Event router | Media worker | None | Downloads audio, stores in blob storage, transcribes |
| `owner.notifications` | Conversation worker, scheduler | Notifications worker | `business_id` | Handoff alerts, daily summary, setup interview |
| `platform.events` | Event router | Platform worker | `business_id` | Account updates, quality changes, disconnections |
| `*.dlq` | Any consumer after max retries | Operator tooling | — | Dead-letter queue per queue for inspection and replay |

### Mechanisms

- **Transactional outbox.** A module writes its state change and its outgoing event in the same database transaction (an outbox table); a relay publishes outbox rows to the queue. No state change exists without its event, and no event without its state change.
- **Per-key ordering.** Jobs with the same ordering key never run concurrently. On Postgres: a transaction-level advisory lock on the key. On Redis Streams: consistent hashing of keys to consumers.
- **Delayed jobs.** Every job has `run_at`. Turn-taking waits, read-receipt delays, typing and split-message delays are delayed jobs, never `sleep()` inside a worker, so workers are never blocked waiting.
- **Supersession.** Jobs carry the `conversation_version` they were planned for. A worker drops any job whose version is older than the conversation's current version. This is how interrupts cancel stale replies without coordination.
- **Retries.** Exponential backoff with jitter, maximum 5 attempts, then the dead-letter queue. Non-retryable errors (validation failures, permanently rejected sends) go straight to the dead-letter queue.
- **Backpressure and fairness.** Per-tenant concurrency caps on `conversation.turns` prevent one busy business from starving others. Queue depth and age are exported as metrics and drive worker autoscaling.
- **Rate limits.** The outbound sender enforces Meta's per-number throughput (5 messages per second for coexistence numbers) with a token bucket per number.

## Multi-tenancy and data

All tenants share one database and one schema, with `business_id` on every tenant-owned row; isolation is enforced by Postgres row-level security and composite foreign keys. The counterparts, schema-per-tenant and database-per-tenant, isolate more strongly but make migrations and operations expensive across thousands of small tenants.

- **Tenant context.** Every unit of tenant work runs in a transaction that first executes `SET LOCAL app.business_id`. The API derives it from the authenticated user; workers derive it from the job. Logs and traces carry it.
- **Two database roles.** `app_user` (row-level security applies) does all tenant work. `app_system` (bypasses row-level security) is used only to resolve a webhook's tenant from Meta's `phone_number_id` and to dequeue jobs.
- **Private data separation.** Floor prices live in their own table; the LLM context builder reads a view without them.
- **Noisy neighbors.** Per-tenant limits on concurrent turns, LLM tokens per day, and outbound messages; limits are plan attributes.
- **Data lifecycle.** Raw webhook events kept 30 days; messages and turns kept for the subscription's life plus 90 days; per-tenant export and hard delete on request.
- **Large tables.** `messages`, `turns`, `webhook_events` and `jobs` are partitioned by month once they pass about 50M rows, so old partitions can be archived or dropped cheaply.
- **Beyond one database.** If a single Postgres primary becomes the limit, shard by `business_id` (for example with Citus); every query already filters by tenant, so the data model does not change.
- **Media.** Audio and images go to blob storage under `/{business_id}/…` keys; the database stores only references.

The reference schema is `db/schema.sql`; the Technical Design summarizes the required data properties.

## Scaling path

Each stage is triggered by a measured limit, not a calendar; every change is configuration or infrastructure, never a data model rewrite.

| Stage | Approximate load | Trigger to move on | Changes |
|---|---|---|---|
| 1. Single machine | Up to 50 businesses, 5k conversations/day | CPU above 70% sustained, or queue age above 5 s at p95 | — |
| 2. Split processes | Up to 500 businesses | Database CPU above 60%, or worker pools contending | Frontend to static host; API, workers and Postgres on separate machines; managed Postgres |
| 3. Horizontal workers | Up to 3,000 businesses | Postgres queue tables become write hotspots | `QUEUE_BACKEND=redis`; worker pools per queue, autoscaled on depth; read replica for dashboard queries |
| 4. Partitioned data | Up to 10,000 businesses | Large tables slow maintenance | Monthly partitions on messages, turns, events; archival of old partitions to blob storage |
| 5. Sharded tenants | Beyond 10,000 businesses | Single primary write limit | Shard by `business_id`; route tenants to shards; extract hottest modules (conversation engine) into separate services if needed |

The LLM provider's rate limits usually bind before the database does: keep provider adapters capable of using several API keys or several providers, and route by tenant plan.

## Security

The three assets to protect are owners' WhatsApp access tokens, customers' conversation data, and floor prices.

| Area | Control |
|---|---|
| Webhook authenticity | Verify Meta's `X-Hub-Signature-256` HMAC with the app secret on every request; reject on mismatch. Verify-token handshake for subscription. |
| Access tokens | Envelope encryption: a data key per tenant encrypted by a master key from a key management service or a secret store; tokens decrypted only inside the outbound sender. |
| Dashboard authentication | Owners sign in with a one-time code sent to their WhatsApp (an authentication template) or email; short-lived access tokens plus rotating refresh tokens. |
| Authorization | Roles: operator, owner, staff. The API sets tenant context from the authenticated identity; clients never send `business_id` for authorization. |
| Tenant isolation | Row-level security and composite foreign keys (see Multi-tenancy). Automated tests attempt cross-tenant reads and writes on every table. |
| LLM safety | Customer text is untrusted input. The LLM has no tools that write data; it returns a structured action that code validates. Floor prices are never in its context. A post-generation validator rejects any reply containing a price or discount not issued by the pricing engine in that turn. |
| Secrets | Environment variables or a secret store only; never in the repository or logs. Logs redact tokens and phone numbers by default. |
| Transport | TLS everywhere; database and queue connections inside a private network in Topology B. |
| Abuse | Rate limits on the public API and auth endpoints; webhook ingress accepts only Meta's payload shape. |
| Audit | Configuration changes (prices, floors, settings) are recorded with actor and timestamp. |

## Reliability and observability

No customer message may go unanswered silently: every failure path ends in a reply, a handoff, or an operator alert.

### Failure handling

- **LLM provider down or slow:** circuit breaker per provider; fall back to a secondary model; if both fail, send a holding message and create a handoff.
- **Meta API errors:** retry transient errors with backoff; on permanent errors (window closed, number restricted) mark the message failed and alert.
- **Worker crash mid-job:** jobs locked longer than their timeout return to the queue; idempotency makes reprocessing safe.
- **Poison messages:** after maximum retries, jobs move to the dead-letter queue with the error, and the conversation is handed off.
- **Database:** point-in-time recovery; restore drill monthly.

### Telemetry

OpenTelemetry for traces, structured JSON logs, Prometheus-style metrics.

| Signal | Examples | Alert when |
|---|---|---|
| Queue health | depth and oldest-job age per queue | age above 30 s for 5 min |
| Turn pipeline | end-of-turn wait, processing latency, sends per turn | processing p95 above 8 s |
| LLM | latency, error rate, tokens and cost per tenant, validator rejections | error rate above 5%, any validator spike |
| Meta | send failures by code, quality rating changes, disconnections | any number drops to medium quality; any disconnection |
| Business outcomes | handoffs, commitments, bot-suspicion questions per tenant | handoff rate doubles day over day |
| Dead-letter queues | count per queue | any new entry |

Every log line, metric and span carries `business_id`, `conversation_id` and `turn_id` where applicable, so one customer's conversation can be traced end to end.
