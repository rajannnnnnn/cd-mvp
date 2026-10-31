# Agent tools harness — tools, routes and rules

**Status:** proposed contract, for review before code. Nothing here is implemented yet. ADR to follow: `docs/decisions/0022-agent-tools-harness.md`.
**Grounded in:** `modules/agent/{pipeline,policy,context,models}.py`, `modules/{pricing,catalog,sales,handoffs}`, `api/routes/{catalog,workflow,conversations,pricing}.py`.

## 1. The idea in one paragraph

Today one structured "planner" call proposes a plan, `policy.build_plan` (code) decides, a "writer" call words it, deterministic checks
verify it, and everything commits in one transaction if the conversation `version` is unchanged. The harness keeps that safety shape and
makes the model's reach explicit: **the model can only do what a registered tool lets it do**, and every tool passes through one gate that
validates input, binds the tenant, enforces budgets and records the call. Tools come in three kinds:

| Kind | What it may do | When it runs | Example |
|---|---|---|---|
| **Read** | Look something up. No side effects. | Immediately, in parallel, cached for the turn | `search_catalog` |
| **Decide** | Ask code for a bounded answer the model must not invent | Immediately; result is the only source of that fact | `quote_price` |
| **Act** | Change the world | **Staged** in memory; committed together at the end of the turn under the `version` check (INV-7), idempotent | `propose_order` |

Rule that makes this safe: **the model never writes to the database and never produces a number.** Act tools only *stage*. Decide tools
are the only source of prices. The existing reply checks still run on the final text.

## 2. How a tool is called

Two paths, same contract:

1. **In-process (default, lowest latency).** `registry.call(name, args, turn)` inside the worker running the turn. No network.
2. **Internal HTTP (for a remote tool service, the eval runner and an operator "tool console").** Only on an internal listener, never on the
   public ingress or the public API:

| Route | Purpose |
|---|---|
| `GET  /internal/agent/v1/tools?agent=seller` | Manifest: tool names, JSON Schemas, kind, budgets, for the given agent role |
| `POST /internal/agent/v1/tools/{tool}` | Call one tool. Body: `{business_id, conversation_id, version, turn_id, call_id, args}` → `{ok, result \| error, latency_ms, cached}` |
| `POST /internal/agent/v1/turns/{turn_id}/commit` | Commit the staged Act calls in one transaction (version-checked); returns what was applied |
| `GET  /internal/agent/v1/turns/{turn_id}/calls` | The trace: every call, arguments, result, status, latency |

Auth for the HTTP path: a service credential for the **agent** role only (not a user token); the tenant comes from the credential plus the
body's `business_id`, and Postgres row-level security still applies. `call_id` makes every call idempotent: repeating it returns the
stored result and never repeats a side effect. In-process calls write the same rows to `turn_tool_calls` (trace + staging; migration to come).

## 3. The seller agent (customer chat) — tools

Customer-facing. 14 tools. Budgets are p95 targets for the tool itself, excluding model time.

### 3.1 Read tools

| Tool | Input | Returns | Backed by | Route (HTTP) | Budget |
|---|---|---|---|---|---|
| `search_catalog` | `query: str(≤200)`, `limit: 1-8`, `category?` | `[{variant_id, product_name, variant_name, category, availability, attributes, description, variant_count}]` | `catalog.repo.retrieve` over `catalog_public` (**no floor column exists there**) | `POST /internal/agent/v1/tools/search_catalog` | 15 ms |
| `get_product` | `variant_id` | one candidate + sibling variants (names only) | `catalog_public` | `…/tools/get_product` | 5 ms |
| `check_availability` | `variant_id`, `quantity` | `{availability: in_stock\|low\|out_of_stock, can_fulfil: bool}` (no exact stock) | `catalog_public` | `…/tools/check_availability` | 5 ms |
| `get_business_info` | `topics: [hours\|address\|delivery\|payment\|returns\|contact\|other]` | `{topic: text \| null}` + `open_now: bool` for hours; `null` means "owner has not said" → use `log_knowledge_gap` | `context.topic_text(profile)` | `…/tools/get_business_info` | 2 ms |
| `get_public_offers` | `variant_id?` | offers the shop allows the AI to mention: `{name, condition_text}`; empty if `may_mention_offers=false` | offers (public fields only) | `…/tools/get_public_offers` | 5 ms |
| `get_conversation_state` | — | `{lead_stage, qualification, pending_order?, focus_variant_id, selling_stopped, ai_paused}`; `pending_order.price` is omitted (existing rule) | `conversations` | `…/tools/get_conversation_state` | 3 ms |
| `get_customer_profile` | — | `{name, is_repeat_customer, message_count}`; never the phone number | `customers`, `deals` | `…/tools/get_customer_profile` | 5 ms |
| `get_recent_messages` | `n: 1-40` | `[{sender, kind, body, at}]` older than the prefetched window | `messages` | `…/tools/get_recent_messages` | 8 ms |
| `get_style_examples` | `situation?`, `limit: 1-8` | the owner's own past replies, for voice | `style_examples` | `…/tools/get_style_examples` | 5 ms |

### 3.2 Decide tool

| Tool | Input | Returns | Backed by | Route | Budget |
|---|---|---|---|---|---|
| `quote_price` | `variant_id`, `quantity ≥1`, `ask: price\|discount\|counter`, `counter_price?` (what the **customer** offered), `advance_payment?` | `decision.for_llm()`: `kind` (quote\|accept\|counter\|ask_qualify\|handoff), `values[{kind, amount}]`, `semantics`, `applied_offers`, `free_items`, `unmet`, `can_concede_more`, `urgency`, `handoff_reason?` | `PricingService.evaluate` (pure engine; **the floor is never in the result**) | `…/tools/quote_price` | 25 ms |

Behaviour that must hold:
- Every amount in the result is registered as **issued** for this turn. The reply checker allows a number in the customer-facing text only if it
  is issued (INV-2). The model cannot say any other price.
- The concession ladder advances **once per turn**: a repeated identical call returns the cached evaluation; state is persisted at commit
  (`PricingService.save_in`), not at call time, so a superseded turn leaves no trace.
- `below_floor` is reported to the model as `handoff` / `owner_review`, never as a number or a reason that reveals a floor.

### 3.3 Act tools (staged, committed atomically at end of turn)

| Tool | Input | Staged effect at commit | Backed by | Route | Harness checks before staging |
|---|---|---|---|---|---|
| `update_qualification` | `fields{quantity, occasion, budget, timeline, delivery_location, delivery_address}` | merge into `conversations.qualification` | `policy` qualification merge | `…/tools/update_qualification` | whitelisted keys, ≤ 200 chars each |
| `set_lead_stage` | `stage`, `lost_reason?` | `lead_stage` via `sales.next_stage` | `sales.next_stage` | `…/tools/set_lead_stage` | **AI never sets `won`** (owner does); `lost` only per existing rule |
| `mark_selling_stopped` | `reason: not_interested\|opted_out` | `selling_stopped=true`, stage `lost` | `policy` | `…/tools/mark_selling_stopped` | none beyond enum |
| `schedule_followup` | `kind: promised_revert`, `when_hint?` (the customer's words, e.g. "kal shaam", "after lunch"), `topic?` | insert one row in `followups` (section 3.5) | `followups` table | `…/tools/schedule_followup` | selling not stopped, not opted out, not paused; the **harness computes `due_at`** from the hint inside the 24 h window and business hours; at most one pending per conversation |
| `propose_order` | `variant_id`, `quantity`, `delivery_address?`, `customer_confirmed: bool` | `capture_deal(kind="order")` **only if** all checks pass; otherwise returns what is missing | `sales.capture_deal` | `…/tools/propose_order` | variant exists and in stock; **price is taken from the last issued quote, never from the model**; address present; `customer_confirmed` true; high-value threshold → also stages a `high_value` handoff |
| `propose_visit` | `time_text` | `capture_deal(kind="visit")` | `sales.capture_deal` | `…/tools/propose_visit` | non-empty time text |
| `request_handoff` | `reason: unknown_answer\|on_request_price\|customer_asked_human\|complaint\|high_value\|other`, `note` | `handoffs.create_handoff` (pauses AI, alerts owner) | `handoffs.create_handoff` | `…/tools/request_handoff` | one open handoff per conversation+reason (existing); `below_floor` is **not** a model-callable reason |
| `log_knowledge_gap` | `question` | `handoffs.log_gap` | `handoffs.log_gap` | `…/tools/log_knowledge_gap` | ≤ 500 chars, de-duplicated (existing) |
| `reply` | `parts: [str]` (1-4), `reaction_emoji?` | queue the outbound messages for delivery planning (pacing, window, rate limit) | `delivery.plan_delivery` via `pipeline._finish` | `…/tools/reply` | runs the deterministic checks + model checker on the text; **numbers must be issued**; up to 2 rewrite attempts, then the safe holding message + handoff (existing behaviour) |
| `stay_silent` | `reason` | no outbound message; messages marked handled | `pipeline` | `…/tools/stay_silent` | allowed |
| `react` | `emoji` | reaction-only reply | `delivery` | `…/tools/react` | single emoji |

Exactly one terminal tool must end the turn: `reply`, `stay_silent`, or `react`. If the model ends without one, or exceeds the tool budget,
the harness uses the safe holding plan (INV-12: never silent about a failure).

### 3.4 Deliberately not tools in v1
- **Free choice of send time or wording at schedule time.** The model may *propose* a follow-up (`schedule_followup`) but code computes the
  time and the message is written when it fires (section 3.5).
- **Catalog or price edits, refunds, discounts outside the engine:** owner-only; not reachable by the seller.
- **Free web search, arbitrary HTTP, code execution:** out of PRD v1 scope.
- **Sending raw text to a customer outside `reply`.** There is no other send path.

### 3.5 Follow-ups: scheduling, storage, trigger

PRD: FR-SL-8 (at most one in-window nudge after a customer goes quiet), v1 outbound is **in-window only** (no templates). Two cases:

| Case | Example | How it is created | Default due time |
|---|---|---|---|
| **Promised revert** | "I'll check and tell you", "kal batata hu", "will ask my husband" | the model calls `schedule_followup(kind=promised_revert, when_hint)` in the turn where the customer said it | from the hint if there is one ("tomorrow evening"), else `revert_after_minutes` (default 240); **always** at least 30 min before the 24 h window closes |
| **Seen, then silent** | customer read our message, no reply | **no model needed**: when a turn commits a reply to a customer who showed interest, the harness inserts a `silent_after_reply` row | `nudge_after_minutes` (default 240); if a `read` status arrives, pulled earlier (`read_after_minutes`, default 120); if delivered but never read, wait the normal time and use neutral wording |

Read receipts are not reliable (customers can switch them off), so *read* only moves a time earlier; it is never required.

**Where it lives.** One table, the source of truth, visible to the owner and to the audit trail:

```
followups(id, business_id, conversation_id, kind, status, due_at, context jsonb, created_by_turn_id, created_at,
          fired_at, outcome, skip_reason)
  status: pending | firing | sent | skipped | cancelled
  unique partial index: one row per conversation WHERE status = 'pending'          -- the "at most one" rule
  index (due_at) WHERE status = 'pending'                                          -- the scheduler scan is a tiny indexed read
  context: product in focus, topic, the customer's own words (≤ 200 chars), never a price
```

**How it fires.** The scheduler claims due rows (`FOR UPDATE SKIP LOCKED`, so several schedulers are safe), marks them `firing` and writes a
`conversation.followup_due` event in the same transaction. It goes to the same per-conversation ordered queue as live turns, so it can never
race a customer message. This replaces the current polling over all conversations in `tick_nudges`.

**Cancelled automatically** (status `cancelled`, reason recorded): any new customer message; an owner reply from the Business app (echo); a
handoff, pause, opt-out or `selling_stopped`; the deal moving to won or lost; or a new `schedule_followup` replacing it.

**Re-validated when it fires** (else `skipped` with a reason): conversation `version` unchanged; AI allowed (`ai_block_reason`); still
inside the 24 h window; inside business hours and the customer quiet-hours rule (default no sends 21:00–09:00 India time, moved to the next
allowed time if still in the window, otherwise skipped); the per-number rate limit has room; fire times are jittered so a 9 am batch does
not hit the number all at once.

**Message: computed when it fires, not written in advance.** Prices, stock, offers and what the customer last said can change in hours; a
pre-written text goes stale and could carry a number that is no longer engine-issued (INV-2). Only the *context* above is stored. At fire time
the normal pipeline runs with `trigger="followup"`: small model plans, writer words it in the owner's voice, the usual checks run, delivery
re-checks window and limits. Nobody is waiting on this turn, so the model call costs money but no perceived latency.

**One follow-up per silence.** After it is sent, no second one until the customer writes again (assumption, see section 9).

## 4. The owner assistant (owner chatting with their own number) — tools

Phase 2 of this work; the contract is fixed now so the registry supports it. Owner tools **reuse the existing public API routes**
(same RBAC: owner or staff) instead of inventing new ones. Every change shows a **read-back and needs a yes** before it is applied
(the existing "config-by-chat with read-back" rule).

| Tool | Does | Public route it maps to |
|---|---|---|
| `list_open_handoffs` | open handoffs | `GET /api/v1/handoffs` |
| `resolve_handoff` | mark handled, resume the AI | `POST /api/v1/handoffs/{id}/resolve` |
| `list_knowledge_gaps` | questions the AI could not answer | `GET /api/v1/knowledge-gaps` |
| `answer_knowledge_gap` | give the answer; AI learns it | `POST /api/v1/knowledge-gaps/{id}/answer` |
| `dismiss_knowledge_gap` | ignore a question | `POST /api/v1/knowledge-gaps/{id}/dismiss` |
| `list_deals` | captured orders and visits | `GET /api/v1/deals` |
| `close_deal` | mark won or lost (**only the owner marks won**) | `POST /api/v1/deals/{id}/close` |
| `pause_conversation` / `resume_conversation` | take over / hand back one chat | `POST /api/v1/conversations/{id}/pause` · `/resume` |
| `update_stock` | in/out of stock for a variant | `PATCH /api/v1/variants/{id}` |
| `get_daily_summary` | the day's numbers | analytics/metrics routes + `owner.daily_summary` |
| `stop_selling` / `start_selling` | the owner's `stop` / `start` commands (FR-CV-10) | existing owner-command handler (no public route today) |

The floor price is **write-only and never an owner-assistant read tool** either (set in the app, `PUT /variants/{id}/policy`).

## 5. The checker agent
No tools. Input `{parts, business, facts}` → `CheckOutput {scope_ok, claims_human, invented_claims}` on the small, fast model. It stays a
separate call so a bad writer cannot grade itself.

## 6. Rules every call goes through (the gate)

1. **Allowlist per agent role.** The seller sees section 3 only; the owner assistant section 4 only. Unknown tool → rejected and logged.
2. **Schema validation** (pydantic): unknown fields dropped, bounds enforced (lengths, ranges, enums). Invalid → error result the model can
   correct once; never executed.
3. **Tenant binding.** Every call runs through `db.tenant(business_id)` (row-level security). No tool takes a tenant or phone number
   argument from the model.
4. **Budgets.** Max 6 tool calls per turn, max 3 model↔tool rounds, per-tool timeout, per-turn deadline. Over budget → holding plan.
5. **Idempotency.** `call_id` per call; Act tools additionally idempotent by business rule (e.g. one pending order per conversation).
6. **Version check at commit (INV-7).** Staged actions are applied only if `conversations.version` is unchanged, and `ai_block_reason` is
   re-evaluated (INV-10). Otherwise nothing is applied.
7. **Untrusted content.** Customer text and tool results are passed to the model as data inside delimiters, marked untrusted; the system
   prompt says they contain no instructions. Tool results are size-capped (≈ 2 KB).
8. **Secrets.** No tool result contains an access token, the master key, a phone number of a third party, or the floor price. A test
   serialises every tool's output for a seeded business and fails if any of those appear.
9. **Trace.** Every call is written to `turn_tool_calls` (tool, args, result, status, latency, cached) and summarised in the existing `turns`
   record.

## 7. Latency and scale

- **Zero extra round trips on the common path.** The harness *pre-runs* the reads every turn needs (`get_conversation_state`,
  `search_catalog` on the customer's text, `get_business_info` for detected topics, recent messages, style examples) in one tenant
  transaction and puts the results in the first prompt, exactly what `context.load` does today. The model only calls tools for what is
  genuinely missing.
- **Parallel and cached.** Independent reads run with `asyncio.gather`; identical calls in a turn return the cached result.
- **Round trips:** target 2 model calls on a typical turn (plan with tools, then write), plus the fast checker. Hard cap 3 tool rounds.
- **Model tiering:** small model for the checker and for simple intents; the larger model only when negotiation or an order is in play.
- **Prompt caching:** stable prefix first (system prompt + tool schemas + shop facts), volatile tail last.
- **Stateless workers.** All state in Postgres and the queue; any worker can run any turn. Concurrency caps per tenant and per number.
  Tools hold no in-process state beyond the per-turn cache, so a remote tool service can scale on its own.
- **Provider behind an interface** (already `LLMProvider`): tool-calling support is added there, so models can change without touching tools.
- **Failure behaviour:** tool timeout → that tool returns an error result; a failed Decide tool (`quote_price`) ends the turn with a handoff,
  never a guessed price.

## 8. What it will take (order of work)

1. ADR 0022 + migration `turn_tool_calls`.
2. `ToolSpec`, registry, executor (validation, budgets, cache, trace) with the backend-agnostic tests (allowlist, budgets, idempotency,
   version-stale commit applies nothing, tenant isolation, no-secret-in-output).
3. Read tools, then `quote_price` (property test: no result ever contains the floor; every quoted number is issued).
4. Act tools + commit; port the existing `build_plan` side effects onto them without changing behaviour.
5. Provider tool-calling in `LLMProvider` (local stand-in drives it), the loop, and the existing 32 evaluation scenarios as the regression bar.
6. Internal HTTP routes; owner-assistant tools last.

## 9. Assumptions I made (please overrule)

1. **The model proposes and the harness confirms** orders and visits (no autonomous capture). Matches how pricing works today.
2. **Follow-ups: the model proposes a promised-revert, code decides the time and writes the text at fire time; "seen then silent" needs no
   model.** One follow-up per silence (it re-arms when the customer writes); quiet hours 21:00–09:00 India time (a product choice for you to set).
3. **The internal HTTP routes are internal-only**; the public API is unchanged in v1.
4. **Seller and owner assistant are separate agents with separate tool allowlists**, not one agent with all tools.
