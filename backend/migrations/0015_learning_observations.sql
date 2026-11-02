-- 0015 — learning loop, recording step (ADR 0031). When the owner answers a conversation the AI handed over, keep the
-- answer WITH its context, so a later step can propose the narrowest scope it applies to. Nothing is learned from these
-- rows yet. Owner-authored text is the only learning source; the customer's words are context, never a source.
-- Per shop only (proposed INV-13): row-level security, and only an allow-list of modules may touch the table.
-- composite foreign keys keep an observation from pointing at another shop's handoff (tenancy property)
ALTER TABLE handoffs ADD CONSTRAINT handoffs_business_id_id_key UNIQUE (business_id, id);

CREATE TABLE learning_observations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
  conversation_id  uuid NOT NULL,
  customer_id      uuid NOT NULL,
  handoff_id       uuid,
  reason           text,                                  -- why the AI handed over
  customer_text    text NOT NULL,                         -- what the customer asked (context only)
  owner_text       text NOT NULL,                         -- the owner's answer (the only learning source)
  source           text NOT NULL CHECK (source IN ('business_app', 'dashboard')),
  context          jsonb NOT NULL DEFAULT '{}',           -- lead stage and anything else that narrows scope
  created_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations (business_id, id) ON DELETE CASCADE,
  FOREIGN KEY (business_id, customer_id) REFERENCES customers (business_id, id) ON DELETE CASCADE,
  FOREIGN KEY (business_id, handoff_id) REFERENCES handoffs (business_id, id) ON DELETE SET NULL (handoff_id)
);
CREATE INDEX learning_observations_conversation_idx ON learning_observations (business_id, conversation_id);
CREATE INDEX learning_observations_customer_idx ON learning_observations (business_id, customer_id);
CREATE INDEX learning_observations_handoff_idx ON learning_observations (business_id, handoff_id);
CREATE INDEX learning_observations_created_idx ON learning_observations (business_id, created_at);

SELECT enable_tenant_rls('learning_observations'::regclass);
