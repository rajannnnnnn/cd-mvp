-- 0029 — usage notices (ADR 0032). A plan is a monthly cap on conversations, never a per-conversation charge; the owner is told in their own
-- WhatsApp when the count reaches 80% and again at 100%, once each per billing period. This table is the memory that keeps it to once: the
-- primary key is (shop, period, threshold), so a second run of the billing cycle, a restart, or two schedulers cannot send a second notice.
CREATE TABLE usage_notices (
  business_id  uuid NOT NULL REFERENCES businesses (id) ON DELETE CASCADE,
  period_start timestamptz NOT NULL,
  threshold    integer NOT NULL CHECK (threshold IN (80, 100)),
  used         integer NOT NULL,
  cap          integer NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, period_start, threshold)
);

SELECT enable_tenant_rls('usage_notices'::regclass);
