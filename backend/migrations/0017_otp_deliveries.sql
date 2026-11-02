-- 0017 — sign-in code delivery requests (ADR 0025). The web component no longer sends WhatsApp messages: it writes a
-- request here and the gateway sends it. The plain code has to reach the gateway somehow, so it is held in this row
-- only until it is sent (cleared on success, failure, expiry and when the web side gives up waiting), and it never
-- appears in an event or job payload (only the id travels). Platform tables: the shop role has no access.
CREATE TABLE otp_deliveries (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  phone       text NOT NULL,
  language    text NOT NULL DEFAULT 'en',
  code        text,                                         -- the plain code, only until sent; NULL afterwards
  status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
  error       text,
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  sent_at     timestamptz
);
CREATE INDEX otp_deliveries_created_idx ON otp_deliveries (created_at);
CREATE INDEX otp_deliveries_pending_idx ON otp_deliveries (expires_at) WHERE status = 'pending';
