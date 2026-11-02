-- 0014 — Meta message usage per shop (ADR 0026). Meta bills per delivered message, and since 1 Oct 2026 each business
-- number has 1,000 free service messages a month. Two facts the schema did not hold are needed to estimate that bill:
--   1. which number sent an owner alert (the shop's own number, or the platform's sender when the shop's is down) —
--      it decides whose free allowance it uses and who pays;
--   2. whether an outbound customer message went through our API or was typed by the owner in the WhatsApp Business
--      app (coexistence echo). Meta bills only the first.

-- 1. owner alerts: the sending number; NULL means the platform's own sender
ALTER TABLE owner_messages ADD COLUMN whatsapp_number_id uuid;
ALTER TABLE owner_messages ADD CONSTRAINT owner_messages_number_fk
  FOREIGN KEY (business_id, whatsapp_number_id) REFERENCES whatsapp_numbers (business_id, id) ON DELETE SET NULL (whatsapp_number_id);
CREATE INDEX owner_messages_number_idx ON owner_messages (business_id, whatsapp_number_id);

-- 2. customer messages: how they were sent
ALTER TABLE messages ADD COLUMN sent_via text NOT NULL DEFAULT 'api' CHECK (sent_via IN ('api', 'business_app'));

-- backfill existing echoes: the owner's own sends, stored with the raw echo event as payload (our API sends store none).
-- The table is FORCE ROW LEVEL SECURITY; the owner role sees no rows unless that is lifted for this one statement.
ALTER TABLE messages NO FORCE ROW LEVEL SECURITY;
UPDATE messages SET sent_via = 'business_app'
 WHERE direction = 'out' AND sender = 'owner' AND payload <> '{}'::jsonb;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;

-- usage reads delivered API sends by month; keep that from scanning every message a shop ever exchanged
CREATE INDEX messages_api_delivered_idx ON messages (business_id, delivered_at)
  WHERE direction = 'out' AND sent_via = 'api';
