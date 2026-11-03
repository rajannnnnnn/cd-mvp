-- 0025 — recovery sweep (ADR 0035): how many times a stuck conversation has been re-driven, and when, so the sweep neither
-- repeats itself every minute nor retries forever.
ALTER TABLE conversations
  ADD COLUMN recovery_attempts int NOT NULL DEFAULT 0,
  ADD COLUMN last_recovery_at  timestamptz;
