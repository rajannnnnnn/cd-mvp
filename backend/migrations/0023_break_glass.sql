-- 0023 — break-glass access (ADR 0028): platform staff read a shop's conversations only under a grant with a stated reason
-- and a short time limit. Platform-only: the shop role has no access to this table (its own view of who looked is audit_log).
CREATE TABLE break_glass_grants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    reason      text NOT NULL CHECK (length(btrim(reason)) >= 10),
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    ended_at    timestamptz
);
CREATE INDEX break_glass_grants_lookup_idx ON break_glass_grants (account_id, business_id, expires_at DESC);
