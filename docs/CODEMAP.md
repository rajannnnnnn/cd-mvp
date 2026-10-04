# Code map and invariant coverage

Where things live, and which automated tests prove each invariant. Keep this current when modules or tests move.

## Backend modules (`backend/src/salesai`)

| Module | Responsibility | Depends on (enforced by `scripts/check_boundaries.py`) |
|---|---|---|
| `tenants` | business creation, accounts, per-tenant key vault (token encryption), export and hard delete | – |
| `catalog` | products, variants, pricing policies, offers, write-only floor writes, retrieval | – |
| `pricing` | **pure** engine (`engine.py`) and the service that loads policy + floor through the pricing DB role | – |
| `sales` | deals (orders and visits), lead-stage rules | – |
| `handoffs` | handoffs, knowledge gaps, owner answers becoming facts | – |
| `channels` | `MessagingChannel` interface, WhatsApp Cloud adapter, simulator network, webhook ingress, Embedded Signup completion | tenants |
| `auth` | number-centric OTP, sessions, rotating refresh tokens, impersonation | channels |
| `delivery` | human-like planner, send-time checks, per-number rate limit, business hours | channels, handoffs |
| `agent` | context assembly, planner → engine → writer → checks pipeline, prompts, LLM providers (`llm/`) | catalog, delivery, handoffs, pricing, sales |
| `conversations` | inbound router, end-of-turn predictor, turn worker | agent, channels, handoffs |
| `notifications` | owner loop: alerts, WhatsApp commands, config-by-chat with read-back, daily summary | agent, catalog, channels, handoffs, sales |

Other packages: `api/` (FastAPI routes under `/api/v1`, SSE hub, error format), `queue/` (interface, Postgres and Redis
backends, worker), `events/` (versioned catalog, outbox, relay), `scheduler.py`, `runtime.py` (composition root),
`config.py` (validated settings), `db.py` (four pools, one per DB role), `obs.py` (JSON logs with redaction, metrics),
`seed.py` (demo data played through the real system).

Process roles of the one image: `api`, `ingress`, `web` (both), `worker`, `scheduler`, `migrate`, `all`.

## Frontend (`frontend/src`)

`site/` marketing page logic (hero, price-limits demo, EN/HI), `auth/` (login, token store), `api/` (generated typed client,
SSE), `app/` (shell, routes), `features/` (dashboard, conversations, pipeline, inbox, catalog, offers, voice, customers,
settings, playground, operator), `ui/` (design system), `i18n/`, `lib/`. Static pages at the root: `index.html`,
`privacy.html`, `terms.html`, `data-deletion.html`; the app is `app/index.html` served under `/app`.

## Invariants → tests

| Invariant | Proven by |
|---|---|
| INV-1 floors never in LLM context, API, logs, exports | `test_tenant_isolation.py` (floor role/function/view tests), `test_api.py::test_floor_price_is_write_only_everywhere`, `::test_openapi_contract_exposes_floor_only_on_input_models`, `::test_export_and_hard_delete_remove_every_trace`, `test_conversation_flow.py::test_floor_never_reaches_the_model_logs_or_records_while_negotiating`, `test_catalog_pricing_service.py`, `test_owner_loop.py::test_floor_prices_cannot_be_set_by_chat`; boundary rule B7; frontend `draft.test.ts` and `e2e/pricing-safety.spec.ts` |
| INV-2 every stated number comes from the engine, checked before sending | `test_pricing_engine.py` (issued-values property), `test_conversation_flow.py::test_invented_price_is_caught_regenerated_then_clean`, `::test_persistently_bad_writer_falls_back_to_holding_message_and_handoff` |
| INV-3 never below floor (unless an owner-allowed offer crosses it) | `test_pricing_engine.py` (hypothesis, mutation-checked), `test_conversation_flow.py::test_negotiation_never_below_floor_and_hands_off_when_customer_insists`, `e2e/playground.spec.ts` |
| INV-4 on-request items get no price | `test_pricing_engine.py`, `test_conversation_flow.py::test_on_request_item_never_gets_a_price_from_the_ai` |
| INV-5 database-enforced tenant isolation | `test_tenant_isolation.py` (every table, view and role), `test_api.py::test_owner_cannot_see_or_touch_another_businesses_data`, `test_inbound.py::test_two_shops_never_mix_conversations` |
| INV-6 durable, idempotent ingestion | `test_inbound.py` (duplicate delivery, reprocessing, bad signature), queue suite durability tests |
| INV-7 no reply for an outdated conversation state | `test_conversation_flow.py::test_new_message_after_planning_discards_the_stale_reply`, `test_inbound.py` supersession tests, `queue_suite.py` supersession tests (both backends) |
| INV-8 24-hour window checked at send time | `test_conversation_flow.py::test_ai_reply_is_never_sent_after_the_24h_window_closes`, `test_api.py` (owner reply 409), `test_owner_loop.py` (template outside the owner's window) |
| INV-9 honesty, scope, no invented urgency | `test_conversation_flow.py` (`identity_question`, `out_of_scope`, reply-check tests) |
| INV-10 opted-out, personal and paused conversations never answered by the AI | `test_conversation_flow.py::test_stop_and_personal_and_disabled_never_get_replies`, `test_inbound.py::test_personal_contact_and_opt_out_are_recorded_but_never_queued_for_a_reply` |
| INV-11 tokens encrypted at rest, no secrets in code/logs | `test_secrets.py`, `test_embedded_signup.py`, `test_config_documented.py` |
| INV-12 every failure ends in a reply, handoff or alert | `test_conversation_flow.py` (provider outage, bad writer), `test_scheduler.py::test_dead_letters_raise_an_operator_alert`, `test_owner_loop.py` (disconnection), window-closed test above |

## Other automated gates

Queue properties on both backends (`queue_suite.py`); the full async pipeline on Redis (`TEST_QUEUE_BACKEND=redis`);
module boundaries (`test_boundaries.py`); API contract drift (`openapi.json` + generated client); the marketing demo's
price schedule against the real engine (`backend/tests/golden/ladder.json`, `test_golden_ladder.py`, `ladder.test.ts`);
Hindi coverage of the marketing site (`hi.test.ts`); configuration documented in `.env.example`
(`test_config_documented.py`); browser tests in `frontend/e2e/`.
