-- 0004 — alert routing: remember which operator alerts have been pushed to the platform team, and how often we tried.
ALTER TABLE operator_alerts
  ADD COLUMN notified_at     timestamptz,
  ADD COLUMN notify_attempts int NOT NULL DEFAULT 0,
  ADD COLUMN notify_error    text;
CREATE INDEX operator_alerts_due_idx ON operator_alerts (id) WHERE resolved_at IS NULL;
