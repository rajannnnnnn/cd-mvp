-- 0013 — pictures on support tickets (ADR 0023). Small images only (PNG, JPEG or WebP, 3 MB each, at most 3 per message), kept in the database
-- behind one table so a later move to object storage only changes how `data` is read and written. Same per-shop row-level security as tickets.
CREATE TABLE support_attachments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id   uuid NOT NULL,
  ticket_id     uuid NOT NULL,
  message_id    bigint NOT NULL REFERENCES support_messages(id) ON DELETE CASCADE,
  content_type  text NOT NULL CHECK (content_type IN ('image/png','image/jpeg','image/webp')),
  size_bytes    integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 3145728),
  data          bytea NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, ticket_id) REFERENCES support_tickets (business_id, id) ON DELETE CASCADE
);
CREATE INDEX support_attachments_message_idx ON support_attachments (message_id);
CREATE INDEX support_attachments_ticket_idx ON support_attachments (business_id, ticket_id);

SELECT enable_tenant_rls('support_attachments'::regclass);
