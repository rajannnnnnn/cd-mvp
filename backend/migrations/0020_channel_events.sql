-- 0020 — channel events: the hand-off from the gateway to the agent (ADR 0025). The gateway reads Meta's webhook and turns it
-- into channel-neutral events; the agent acts on them. They meet here, in a mailbox, so the agent never sees the wire format
-- and the gateway never writes conversations. Only the event id travels in the queue (a message can carry anything a customer
-- typed). A shop table (row-level security). (webhook_event_id, ordinal, number_id) is unique, so handing over the same
-- webhook twice - or retrying one that failed half way - never duplicates an event.
CREATE TABLE channel_events (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
  number_id        uuid NOT NULL,
  webhook_event_id bigint,                                         -- the raw request it came from (kept for replay, FR-OP-3)
  ordinal          integer NOT NULL DEFAULT 0,                      -- its position within that request
  kind             text NOT NULL CHECK (kind IN ('inbound', 'echo', 'status', 'account')),
  event            jsonb NOT NULL,
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done')),
  error            text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  handled_at       timestamptz,
  FOREIGN KEY (business_id, number_id) REFERENCES whatsapp_numbers (business_id, id) ON DELETE CASCADE,
  UNIQUE (webhook_event_id, ordinal, number_id)
);
CREATE INDEX channel_events_number_idx ON channel_events (business_id, number_id);
CREATE INDEX channel_events_done_idx ON channel_events (created_at) WHERE status = 'done';

SELECT enable_tenant_rls('channel_events'::regclass);
