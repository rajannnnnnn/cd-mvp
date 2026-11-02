-- 0018 — number connect requests (ADR 0025). Completing Embedded Signup needs Meta's Graph API, the app secret and the
-- master key for the token. The web component holds none of them any more: it writes a request here and the gateway
-- performs it. Meta's authorization code is single-use and short-lived; it is held only until used (cleared when the
-- request finishes or expires) and never travels in an event or job - only the request id does.
-- A shop table (row-level security): the owner's request is theirs and nobody else's.
CREATE TABLE number_connect_requests (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
  code             text,                                              -- Meta's one-time code; NULL once finished
  waba_id          text NOT NULL,
  phone_number_id  text NOT NULL,
  coexistence      boolean NOT NULL,
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
  number_id        uuid,                                              -- the connected number, once done
  error_status     integer,                                           -- how the web side answers the owner (HTTP status)
  error_code       text,
  error_message    text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  FOREIGN KEY (business_id, number_id) REFERENCES whatsapp_numbers (business_id, id) ON DELETE SET NULL (number_id)
);
CREATE INDEX number_connect_requests_business_idx ON number_connect_requests (business_id, created_at);
CREATE INDEX number_connect_requests_number_idx ON number_connect_requests (business_id, number_id);
CREATE INDEX number_connect_requests_pending_idx ON number_connect_requests (created_at) WHERE status = 'pending';

SELECT enable_tenant_rls('number_connect_requests'::regclass);
