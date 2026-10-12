-- 0010 — Foreign keys without an index make every delete or update of the parent scan the child, and every lookup by that column a table scan.
-- Found by comparing each foreign key with the indexes on its table, with 3 million messages loaded (loadtest/scale_data.py).
CREATE INDEX IF NOT EXISTS deals_conversation_idx           ON deals (business_id, conversation_id);
CREATE INDEX IF NOT EXISTS handoffs_conversation_idx        ON handoffs (business_id, conversation_id);
CREATE INDEX IF NOT EXISTS messages_turn_idx                ON messages (business_id, turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS offers_variant_idx               ON offers (business_id, variant_id) WHERE variant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS negotiations_variant_idx         ON negotiations (business_id, variant_id);
CREATE INDEX IF NOT EXISTS knowledge_gaps_conversation_idx  ON knowledge_gaps (business_id, conversation_id) WHERE conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS knowledge_gaps_biz_idx           ON knowledge_gaps (business_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS business_users_account_idx       ON business_users (account_id);
CREATE INDEX IF NOT EXISTS auth_sessions_business_idx       ON auth_sessions (business_id) WHERE business_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS operator_alerts_business_idx     ON operator_alerts (business_id) WHERE business_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS style_examples_business_idx      ON style_examples (business_id);
CREATE INDEX IF NOT EXISTS support_messages_ticket_biz_idx  ON support_messages (business_id, ticket_id, id);
