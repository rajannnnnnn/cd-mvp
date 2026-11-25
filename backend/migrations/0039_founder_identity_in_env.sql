-- 0039 — the founder's identity lives in the environment (ADR 0056). The authenticator secret is no longer stored: it is FOUNDER_TOTP_SECRET, read at
-- every sign-in, so wiping or restoring the database loses nothing. The row keeps only state (last used code step, counters, lock, trusted browsers).
ALTER TABLE admin_credentials ALTER COLUMN totp_secret_enc DROP NOT NULL;

-- What a console session and a sign-in ticket remember (ADR 0048, extended): the identity settings it was opened under (a fingerprint, so a session ends the
-- moment any of phone, email or authenticator secret changes in the deployment), how risky the login was judged, every proof used, and where it came from
-- (city and region only when the front proxy sends them; the browser's language). A ticket also holds how many codes it still needs and when each was sent.
ALTER TABLE admin_sessions
  ADD COLUMN identity  text,                         -- first 24 hex of a hash of FOUNDER_PHONE | FOUNDER_EMAIL | FOUNDER_TOTP_SECRET
  ADD COLUMN risk      text CHECK (risk IN ('routine', 'elevated', 'high')),
  ADD COLUMN proofs    text,                         -- in order: authenticator,whatsapp,email
  ADD COLUMN reasons   text,                         -- why the login was not routine: first_sign_in, new_device, new_network, wrong_codes, locked
  ADD COLUMN needs     smallint,                     -- tickets only: how many out-of-band codes (WhatsApp, email) the login needs
  ADD COLUMN sent      jsonb NOT NULL DEFAULT '{}',  -- tickets only: when each channel's code was last sent (a short wait before sending again)
  ADD COLUMN city      text,
  ADD COLUMN region    text,
  ADD COLUMN language  text;

-- One-time codes sent to the founder's email. Only a hash is kept; a code works once, for ten minutes, five tries. Console-only table.
CREATE TABLE admin_email_codes (
  id          bigserial PRIMARY KEY,
  account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  ticket_id   uuid NOT NULL REFERENCES admin_sessions(id) ON DELETE CASCADE,
  code_hash   text NOT NULL,
  expires_at  timestamptz NOT NULL,
  attempts    smallint NOT NULL DEFAULT 0,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX admin_email_codes_ticket_idx ON admin_email_codes (ticket_id);
CREATE INDEX admin_email_codes_account_idx ON admin_email_codes (account_id);
