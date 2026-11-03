-- 0026 — the agent audit (ADR 0037): one row each time the AI is woken, written when the run starts and finished when it ends, so a
-- run that was killed halfway is visible too. What woke it, how it routed and decided, what its model calls cost in total, and how it
-- ended, including "no reply" and why. `route` and `llm` hold decisions, counts and costs only: never a customer's words, name or
-- number, and never a price or floor. A shop table (row-level security); the platform reads it as aggregates.
CREATE TABLE agent_runs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
  conversation_id  uuid NOT NULL,
  trigger          text NOT NULL CHECK (trigger IN ('turn_detection', 'scheduler_nudge')),
  trigger_detail   jsonb NOT NULL DEFAULT '{}',      -- turn detection: why it judged the customer done, how long it waited, how many messages
  status           text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'finished', 'interrupted')),
  outcome          text CHECK (outcome IN ('replied', 'reaction_only', 'no_reply', 'blocked', 'superseded', 'failed')),
  reason           text,
  turn_id          uuid,
  route            jsonb NOT NULL DEFAULT '{}',
  llm              jsonb NOT NULL DEFAULT '{}',      -- every model call of the run added up, and split by step
  first_inbound_at timestamptz,                      -- when the customer's first unanswered message arrived (null for a nudge)
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations (business_id, id) ON DELETE CASCADE
);
CREATE INDEX agent_runs_business_idx     ON agent_runs (business_id, started_at DESC);
CREATE INDEX agent_runs_conversation_idx ON agent_runs (business_id, conversation_id, started_at);
CREATE INDEX agent_runs_started_idx      ON agent_runs (started_at DESC);
CREATE INDEX agent_runs_open_idx         ON agent_runs (started_at) WHERE status = 'running';

SELECT enable_tenant_rls('agent_runs'::regclass);
