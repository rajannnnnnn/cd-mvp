-- 0022 — the admin plane (ADR 0028): an authenticator enrolled per platform operator, and the console's own short sessions.
-- Neither table has a business_id: they belong to the platform, not to a shop, and the shop role cannot see them.
CREATE TABLE admin_credentials (
    account_id     uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    totp_secret_enc bytea NOT NULL,                       -- encrypted under a key only the admin part can derive
    enrolled_at    timestamptz NOT NULL DEFAULT now(),
    last_used_step bigint NOT NULL DEFAULT 0,             -- the newest authenticator step already accepted: a code works once
    failed_attempts int NOT NULL DEFAULT 0,
    locked_until   timestamptz
);
CREATE TABLE admin_sessions (
    id          uuid PRIMARY KEY,
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    stage       text NOT NULL CHECK (stage IN ('ticket', 'session')),   -- a ticket (WhatsApp code accepted) is not a session
    ip          text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz
);
CREATE INDEX admin_sessions_account_idx ON admin_sessions (account_id, created_at DESC);
