-- 0021 — platform alerts (ADR 0029): four severities (low, medium, high, critical), one open alert per cause with a count,
-- and the bookkeeping the router needs for cooldowns, rollups and recovery notices.
ALTER TABLE operator_alerts DROP CONSTRAINT operator_alerts_severity_check;
UPDATE operator_alerts SET severity = CASE severity WHEN 'info' THEN 'low' WHEN 'warning' THEN 'medium' ELSE severity END;
ALTER TABLE operator_alerts
  ADD CONSTRAINT operator_alerts_severity_check CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  ALTER COLUMN severity SET DEFAULT 'medium',
  ADD COLUMN dedup_key            text        NOT NULL DEFAULT '',          -- the cause within a kind (a queue, a phone-number id)
  ADD COLUMN occurrences          int         NOT NULL DEFAULT 1,
  ADD COLUMN last_seen_at         timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN notified_occurrences int         NOT NULL DEFAULT 0,           -- how many occurrences the last email already counted
  ADD COLUMN recovery_notified_at timestamptz;
-- Alerts raised before this change were not grouped: keep the newest open one of each cause, close the rest.
UPDATE operator_alerts a SET resolved_at = now()
 WHERE a.resolved_at IS NULL
   AND EXISTS (SELECT 1 FROM operator_alerts b WHERE b.resolved_at IS NULL AND b.kind = a.kind
                  AND b.business_id IS NOT DISTINCT FROM a.business_id AND b.id > a.id);
UPDATE operator_alerts SET notified_occurrences = occurrences WHERE notified_at IS NOT NULL;
UPDATE operator_alerts SET recovery_notified_at = now() WHERE resolved_at IS NOT NULL;     -- history is not announced as news
CREATE UNIQUE INDEX operator_alerts_open_cause_idx ON operator_alerts (kind, (COALESCE(business_id::text, '')), dedup_key) WHERE resolved_at IS NULL;
