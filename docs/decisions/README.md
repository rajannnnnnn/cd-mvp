# Architecture decision records

One file per significant decision: context, options, decision, consequences, how to revisit. Decisions are
superseded by a new ADR, never rewritten. Status is `accepted` unless stated.

| # | Decision |
|---|---|
| [0001](0001-stack.md) | Backend and frontend stack |
| [0002](0002-modular-monolith-and-boundaries.md) | Modular monolith, process roles, CI-enforced boundaries |
| [0003](0003-tenancy-and-database-roles.md) | Tenant isolation by Postgres RLS, four database roles, floors in their own table |
| [0004](0004-queue.md) | Queue abstraction with Postgres and Redis backends |
| [0005](0005-events-and-outbox.md) | Versioned event envelope with a transactional outbox |
| [0006](0006-inv1-floor-prices-as-knowledge.md) | How INV-1 treats a floor that surfaces as an issued quote (**founder to confirm**) |
| [0007](0007-llm-pipeline-and-local-stand-in.md) | LLM pipeline, vendor interface, and the deterministic local stand-in |
| [0008](0008-number-centric-authentication.md) | Number-centric authentication |
| [0009](0009-channels-and-simulator.md) | Channel adapters and the signed-webhook simulator |
| [0010](0010-static-checks.md) | Lint, types and the boundary checker as CI gates |
| [0011](0011-frontend-and-hosting.md) | Client-side-rendered app plus static marketing site; static hosting |
| [0012](0012-deployment-shape.md) | Containers, compose, reverse proxy, demo vs production configuration |
| [0013](0013-pricing-engine.md) | Pure pricing engine and the concession schedule |
| [0014](0014-delivery-pacing.md) | Human-like delivery planner and send-time checks |
| [0015](0015-voice-notes-deferred.md) | Voice-note transcription deferred by the founder; the seam is kept |
| [0016](0016-per-conversation-ordering-and-flood-guard.md) | Outbound ordering per conversation; per-conversation AI turn budget |
| [0017](0017-alert-routing.md) | Alert routing to the platform team |
| [0018](0018-self-serve-signup-onboarding-slugs.md) | Self-serve sign-up, onboarding and shop addresses |
| [0019](0019-billing.md) | Billing: plans, trial, GST invoices, payment provider port |
| [0020](0020-support-tickets.md) | Support tickets from Settings; operators read ticket text |
| [0020](0020-channel-gateway.md) | Channel gateway: receive, inbound, send; Meta tokens reachable only by `send` |
| [0021](0021-table-domains-plan.md) | Grouping the tables into `platform` / `shop` / `convo` / `infra` schemas (step 1 locked for before pilot) |
| [0022](0022-loading-skeletons.md) | Loading skeletons |
| [0023](0023-support-pictures.md) | Pictures on support tickets |
| 0024 | **Reserved** — client-rendered marketing site; the file exists only on the founder's machine (commit `ef05e4f`, unpushed). Do not reuse this number. |
| [0025](0025-three-components-and-their-contract.md) | Three components (web, gateway, agent) plus a migration job, and the contract between them |
| [0026](0026-channel-cost-is-a-budget.md) | Meta per-message cost is counted and budgeted; reply parts are a per-tenant budget |
| [0027](0027-ai-priorities-caching-and-evals.md) | AI priorities: security, answer quality, latency, cost; prompt caching; evals measure selling too |
| [0028](0028-admin-plane-and-founder-dashboard.md) | Founder dashboard on its own origin and API role; aggregates by default, break-glass audited |
| [0029](0029-platform-alerting-to-the-founder.md) | Platform alerts by email to the founder only, with severity, grouping and cooldowns |
| [0030](0030-audit-retention-and-dpdp.md) | Append-only audit log, declared retention schedule, DPDP processor posture |
| [0031](0031-per-tenant-learning-loop.md) | Per-tenant learning loop: propose the narrowest scope, never assume; owner-authored sources only |
| [0032](0032-plan-packaging-by-conversations.md) | Plans sold by conversations included per month, with overage |
| [0033](0033-two-question-pricing-setup.md) | Pricing setup reduced to two questions; the rest derived, advanced fields kept |

**Numbering note.** Two ADRs share the number **0020** (`0020-support-tickets.md` and
`0020-channel-gateway.md`). Published files are not renumbered; both are listed above. **0024** is
reserved as noted. New ADRs continue from **0034**.
