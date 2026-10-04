-- 0006 — billing: one subscription per business, GST invoices, payments. Plans themselves are a versioned catalogue in code
-- (modules/billing/plans.py) so changing a price never needs a migration. Money is stored in paise (integers).
CREATE TABLE subscriptions (
  business_id          uuid PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
  plan                 text NOT NULL,
  status               text NOT NULL CHECK (status IN ('trialing','active','past_due','canceled','pilot')),
  billing_interval     text NOT NULL DEFAULT 'month' CHECK (billing_interval IN ('month','year')),
  trial_ends_at        timestamptz,
  current_period_start timestamptz,
  current_period_end   timestamptz,
  cancel_at_period_end boolean NOT NULL DEFAULT false,
  pending_plan         text,                 -- a downgrade that takes effect at the end of the paid period
  pending_interval     text CHECK (pending_interval IN ('month','year')),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER subscriptions_touch BEFORE UPDATE ON subscriptions FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE SEQUENCE invoice_number_seq;
CREATE TABLE invoices (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id   uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  number        text NOT NULL UNIQUE,
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid','void')),
  plan          text NOT NULL,
  billing_interval text NOT NULL DEFAULT 'month' CHECK (billing_interval IN ('month','year')),
  period_start  timestamptz NOT NULL,
  period_end    timestamptz NOT NULL,
  lines         jsonb NOT NULL,              -- [{description, quantity, unit_paise, amount_paise}]
  subtotal_paise bigint NOT NULL CHECK (subtotal_paise >= 0),
  gst_paise     bigint NOT NULL CHECK (gst_paise >= 0),
  total_paise   bigint NOT NULL CHECK (total_paise >= 0),
  currency      text NOT NULL DEFAULT 'INR',
  issued_at     timestamptz NOT NULL DEFAULT now(),
  due_at        timestamptz NOT NULL,
  paid_at       timestamptz,
  UNIQUE (business_id, id)
);
CREATE INDEX invoices_business_idx ON invoices (business_id, issued_at DESC);

CREATE TABLE payments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id   uuid NOT NULL,
  invoice_id    uuid NOT NULL,
  provider      text NOT NULL,
  provider_ref  text NOT NULL,
  amount_paise  bigint NOT NULL CHECK (amount_paise >= 0),
  status        text NOT NULL CHECK (status IN ('pending','succeeded','failed')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, invoice_id) REFERENCES invoices (business_id, id) ON DELETE CASCADE,
  UNIQUE (provider, provider_ref)
);

SELECT enable_tenant_rls(t::regclass) FROM unnest(ARRAY['subscriptions','invoices','payments']) AS t;
