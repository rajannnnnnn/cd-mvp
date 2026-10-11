-- 0008 — support tickets: an owner opens one from Settings and it exists at once; support answers in the same thread.
-- status: open = waiting for support, answered = waiting for the owner, resolved = done (a new message from the owner reopens it).
CREATE TABLE support_tickets (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id     uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  number          bigint GENERATED ALWAYS AS IDENTITY,
  opened_by       uuid REFERENCES accounts(id) ON DELETE SET NULL,
  subject         text NOT NULL CHECK (length(subject) BETWEEN 1 AND 160),
  category        text NOT NULL CHECK (category IN ('question','problem','billing','whatsapp','other')),
  status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','resolved')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, id)
);
CREATE INDEX support_tickets_business_idx ON support_tickets (business_id, updated_at DESC);
CREATE INDEX support_tickets_open_idx ON support_tickets (status, updated_at DESC);

CREATE TABLE support_messages (
  id              bigserial PRIMARY KEY,
  business_id     uuid NOT NULL,
  ticket_id       uuid NOT NULL,
  author          text NOT NULL CHECK (author IN ('shop','support')),
  author_account  uuid REFERENCES accounts(id) ON DELETE SET NULL,
  body            text NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, ticket_id) REFERENCES support_tickets (business_id, id) ON DELETE CASCADE
);
CREATE INDEX support_messages_ticket_idx ON support_messages (ticket_id, id);

SELECT enable_tenant_rls(t::regclass) FROM unnest(ARRAY['support_tickets','support_messages']) AS t;
