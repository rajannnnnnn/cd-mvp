-- 0012 — channel gateway raw log (ADR 0020 follow-up 1). Keep the exact request bytes and the signature header so a request can be
-- re-verified or replayed byte for byte, and keep requests that failed verification (flagged signature_valid = false, size-capped,
-- shorter retention). Those rows have no parsed payload and are never queued.
ALTER TABLE webhook_events ADD COLUMN raw_body bytea, ADD COLUMN signature text;
ALTER TABLE webhook_events ALTER COLUMN payload DROP NOT NULL;
