-- 0037 — a record of every attempt to tell the founder (ADR 0053): which way, whether it was accepted, and the provider's own reference
-- (for Amazon SES, the message id to look up in the SES console). Never the text of the alert, never an address: only what is needed to answer
-- "did it go out, and when". Platform table: the shop logins cannot read it.
CREATE TABLE alert_deliveries (
  id          bigserial PRIMARY KEY,
  alert_id    bigint REFERENCES operator_alerts(id) ON DELETE SET NULL,   -- null for a digest that gathers several
  kind        text NOT NULL CHECK (kind IN ('alert', 'digest', 'recovery')),
  severity    text NOT NULL,
  sink        text NOT NULL,                                              -- email | webhook | whatsapp
  ok          boolean NOT NULL,
  provider_ref text,                                                      -- e.g. the SES message id
  error       text,
  items       integer NOT NULL DEFAULT 1,                                 -- how many alerts this one message carried
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX alert_deliveries_created_idx ON alert_deliveries (created_at DESC);
CREATE INDEX alert_deliveries_alert_idx ON alert_deliveries (alert_id);
