-- 0038 — mail to shop owners (ADR 0054): payment confirmations, support case notices, and the like.
-- A shop gives ONE optional address for billing and support mail. Without it nothing is sent (the in-app and WhatsApp notices stay).
ALTER TABLE businesses ADD COLUMN billing_email text
  CHECK (billing_email IS NULL OR billing_email ~ '^[^@[:space:],;<>]+@[^@[:space:],;<>]+\.[^@[:space:],;<>]+$');

-- The outbox for that mail. A row is written in the SAME transaction as the thing it is about (the payment settling, the ticket reply), so the
-- mail can never be lost and can never hold the payment up; the web scheduler sends it afterwards, with retries. `dedupe_key` makes a repeated
-- event (a retried webhook, a double click) one mail. The row holds only what the template needs: never the address (read at send time) and
-- never a ticket's text (the mail links to the app).
CREATE TABLE mail_outbox (
  id              bigserial PRIMARY KEY,
  business_id     uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  template        text NOT NULL,                       -- a name in modules/mail/templates.py
  purpose         text NOT NULL CHECK (purpose IN ('billing', 'support', 'account', 'updates')),
  data            jsonb NOT NULL DEFAULT '{}'::jsonb,
  dedupe_key      text NOT NULL UNIQUE,
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
  attempts        integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  provider_ref    text,                                 -- e.g. the SES message id
  error           text,                                 -- why it failed or was skipped (never an address)
  created_at      timestamptz NOT NULL DEFAULT now(),
  sent_at         timestamptz
);
CREATE INDEX mail_outbox_due_idx ON mail_outbox (next_attempt_at) WHERE status = 'pending';
CREATE INDEX mail_outbox_recent_idx ON mail_outbox (id DESC);
CREATE INDEX mail_outbox_business_idx ON mail_outbox (business_id);
