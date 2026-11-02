-- 0016 — platform audit trail (ADR 0030, ADR 0028). Platform actions on a shop — export, deletion, impersonation,
-- operator changes, and later the founder dashboard's break-glass reads — must leave a record that outlives the shop.
-- A shop's own audit_log is erased with the shop under DPDP deletion, which used to erase the record of the deletion
-- itself. No foreign keys on purpose: the trail must survive the shop and the account. Keep `detail` free of personal
-- data (ids, counts, field names), since it persists after a shop is erased.
CREATE TABLE platform_audit_log (
  id                bigserial PRIMARY KEY,
  actor_account_id  uuid,
  actor_label       text NOT NULL,
  action            text NOT NULL,
  business_id       uuid,
  detail            jsonb NOT NULL DEFAULT '{}',
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX platform_audit_log_business_idx ON platform_audit_log (business_id, created_at);
CREATE INDEX platform_audit_log_created_idx ON platform_audit_log (created_at);
