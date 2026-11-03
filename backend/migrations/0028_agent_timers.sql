-- 0028 — the agent's own clock (ADR 0038): at most one pending timer of each kind per conversation, set, moved or cancelled as things
-- happen. Plus the audit's trigger names: a follow-back and a take-over are what wake the AI now, not a scan.
CREATE TABLE agent_timers (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
  conversation_id  uuid NOT NULL,
  kind             text NOT NULL CHECK (kind IN ('follow_back', 'takeover_check')),
  due_at           timestamptz NOT NULL,
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'fired', 'cancelled')),
  reason           text NOT NULL,                          -- what set it: ai_replied, owner_reply, handoff, manual, customer_waiting
  why              text NOT NULL,                          -- one plain sentence for the audit; no customer content
  detail           jsonb NOT NULL DEFAULT '{}',            -- follow_back: the customer message it is for
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  fired_at         timestamptz,
  cancelled_at     timestamptz,
  cancel_reason    text,
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations (business_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX agent_timers_one_pending_idx ON agent_timers (conversation_id, kind) WHERE status = 'pending';
CREATE INDEX agent_timers_due_idx ON agent_timers (due_at) WHERE status = 'pending';
CREATE INDEX agent_timers_conversation_idx ON agent_timers (business_id, conversation_id);
SELECT enable_tenant_rls('agent_timers'::regclass);

ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_trigger_check;
UPDATE agent_runs SET trigger = 'follow_back' WHERE trigger = 'scheduler_nudge';
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_trigger_check CHECK (trigger IN ('turn_detection', 'follow_back', 'takeover'));
