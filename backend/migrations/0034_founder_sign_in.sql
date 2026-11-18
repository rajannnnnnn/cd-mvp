-- 0034 — founder sign-in (ADR 0047). What the console needs to tell a routine sign-in from a suspicious one: how many wrong codes since the last
-- good sign-in, when the last wrong code was, where and when the last good sign-in came from, and the browsers the founder has trusted (hashes only,
-- never the token itself). Console-only table: the shop and platform logins cannot read it.
ALTER TABLE admin_credentials
  ADD COLUMN failed_since_login int NOT NULL DEFAULT 0,        -- wrong codes since the last successful sign-in (not reset by a lock, only by a good sign-in)
  ADD COLUMN last_failed_at     timestamptz,
  ADD COLUMN last_login_at      timestamptz,
  ADD COLUMN last_login_ip      text,
  ADD COLUMN was_locked         boolean NOT NULL DEFAULT false,  -- locked since the last good sign-in
  ADD COLUMN trusted_devices    jsonb NOT NULL DEFAULT '[]';     -- [{"h": sha256 of the device token, "at": when it was trusted}], at most five
