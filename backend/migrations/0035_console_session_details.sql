-- 0035 — console sessions remember how they started (ADR 0048): where from, on what, how it was proven, when last used, and why it ended.
-- Console-only table: the shop and platform logins cannot read it.
ALTER TABLE admin_sessions
  ADD COLUMN user_agent      text,                  -- the browser's own description, truncated
  ADD COLUMN device_label    text,                  -- "Chrome 142 on Windows 11"
  ADD COLUMN device_id       text,                  -- first 16 hex of the trusted-browser hash (never the token); ties sessions to one browser
  ADD COLUMN geo             text,                  -- country from the front proxy's header, when one is configured
  ADD COLUMN network         text,                  -- the /24 (IPv4) or /48 (IPv6) the address belongs to
  ADD COLUMN method          text,                  -- 'authenticator' | 'authenticator+whatsapp' | 'extended'
  ADD COLUMN step_up_reason  text,                  -- why the WhatsApp code was asked for (first_sign_in, new_device, ...)
  ADD COLUMN last_seen_at    timestamptz,
  ADD COLUMN ended_reason    text CHECK (ended_reason IN ('signed_out', 'revoked', 'revoked_all', 'extended', 'enrolment'));
CREATE INDEX admin_sessions_account_created_idx ON admin_sessions (account_id, created_at DESC);
