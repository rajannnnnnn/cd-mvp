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
