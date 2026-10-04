-- 0002_api_support — idempotent writes + live-update notifications for the API.

-- Idempotency for retried writes (Technical Design: External API). Tenant-scoped, RLS enforced.
CREATE TABLE idempotency_keys (
  business_id   uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  key           text NOT NULL,
  request_hash  text NOT NULL,
  status        integer NOT NULL,
  response      jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, key)
);
SELECT enable_tenant_rls('idempotency_keys');

-- Live updates: row changes announce themselves; each API instance LISTENs and fans out to its own SSE clients.
CREATE OR REPLACE FUNCTION notify_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE conv uuid;
BEGIN
  IF TG_TABLE_NAME = 'conversations' THEN conv := NEW.id;
  ELSIF TG_TABLE_NAME IN ('messages','handoffs','deals') THEN conv := NEW.conversation_id;
  END IF;
  PERFORM pg_notify('salesai_changes', json_build_object('b', NEW.business_id, 't', TG_TABLE_NAME, 'c', conv)::text);
  RETURN NULL;
END $$;

CREATE TRIGGER messages_notify      AFTER INSERT OR UPDATE ON messages      FOR EACH ROW EXECUTE FUNCTION notify_change();
CREATE TRIGGER conversations_notify AFTER INSERT OR UPDATE ON conversations FOR EACH ROW EXECUTE FUNCTION notify_change();
CREATE TRIGGER handoffs_notify      AFTER INSERT OR UPDATE ON handoffs      FOR EACH ROW EXECUTE FUNCTION notify_change();
CREATE TRIGGER deals_notify         AFTER INSERT OR UPDATE ON deals         FOR EACH ROW EXECUTE FUNCTION notify_change();
CREATE TRIGGER gaps_notify          AFTER INSERT OR UPDATE ON knowledge_gaps FOR EACH ROW EXECUTE FUNCTION notify_change();
