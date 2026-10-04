-- =====================================================================
-- 0001_core — multi-tenant schema for the WhatsApp AI Sales Assistant
-- Postgres 15+. Run as app_owner by `python -m salesai migrate`.
--
-- Tenancy: shared database + schema, business_id on every tenant-owned row.
--   1. Composite foreign keys (business_id, id): a row can never reference
--      another tenant's row, even through an application bug.   (INV-5)
--   2. Row-Level Security, FORCEd, keyed on app.business_id.      (INV-5)
-- Private pricing: price_floors is readable ONLY by role app_pricing.  (INV-1)
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE FUNCTION current_business_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.business_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END $$;

-- Applies the standard tenant-isolation policy to a table (used by every migration).
CREATE OR REPLACE FUNCTION enable_tenant_rls(tbl regclass, col text DEFAULT 'business_id') RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
  EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
  EXECUTE format('CREATE POLICY tenant_isolation ON %s USING (%I = current_business_id()) WITH CHECK (%I = current_business_id())', tbl, col, col);
END $$;

-- =====================================================================
-- IDENTITY (number-centric): one account per phone number
-- =====================================================================

CREATE TABLE accounts (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  phone          text NOT NULL UNIQUE CHECK (phone ~ '^\+[1-9][0-9]{7,14}$'),   -- E.164
  name           text,
  platform_role  text CHECK (platform_role IN ('operator')),
  status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','blocked')),
  language       text NOT NULL DEFAULT 'en' CHECK (language IN ('en','hi')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  last_login_at  timestamptz
);

CREATE TABLE otp_challenges (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  phone        text NOT NULL,
  code_hash    text NOT NULL,            -- HMAC of the code; the code itself is never stored
  expires_at   timestamptz NOT NULL,
  attempts     integer NOT NULL DEFAULT 0,
  consumed_at  timestamptz,
  ip           text,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX otp_phone_idx ON otp_challenges (phone, created_at DESC);
CREATE INDEX otp_ip_idx    ON otp_challenges (ip, created_at DESC);

-- =====================================================================
-- TENANTS
-- =====================================================================

CREATE TABLE businesses (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                  text NOT NULL,
  timezone              text NOT NULL DEFAULT 'Asia/Kolkata',
  status                text NOT NULL DEFAULT 'onboarding'
                        CHECK (status IN ('onboarding','active','paused','churned')),
  ai_enabled            boolean NOT NULL DEFAULT false,
  plan                  text NOT NULL DEFAULT 'pilot',
  -- Facts layer: address, hours, delivery areas, payment modes, policies, free-form facts.
  profile               jsonb NOT NULL DEFAULT '{}',
  -- Sales style: proactiveness, may_mention_offers, honorifics, handoff triggers.
  sales_settings        jsonb NOT NULL DEFAULT '{}',
  -- Turn-taking: max_wait_ms, owner_pause_minutes, business_hours_behavior, nudge_after_minutes.
  conversation_settings jsonb NOT NULL DEFAULT '{}',
  -- Human-like pacing distributions (learnable per business): read, typing, gaps, cap.
  timing_params         jsonb NOT NULL DEFAULT '{}',
  -- Plan limits: max_concurrent_turns, llm_tokens_per_day, outbound_per_day.
  limits                jsonb NOT NULL DEFAULT '{}',
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);

-- Per-tenant data key for envelope encryption of access tokens (INV-11). Platform-only.
CREATE TABLE business_keys (
  business_id  uuid PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
  dek_wrapped  bytea NOT NULL,           -- data key encrypted by the master key (env/secret store)
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE business_users (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  account_id       uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name             text NOT NULL,
  role             text NOT NULL DEFAULT 'owner' CHECK (role IN ('owner','staff')),
  notify           boolean NOT NULL DEFAULT true,     -- receives handoff alerts / daily summary
  last_inbound_at  timestamptz,                       -- owner's own 24h window for alerts
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, id),
  UNIQUE (business_id, account_id)
);

CREATE TABLE whatsapp_numbers (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id       uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  channel           text NOT NULL DEFAULT 'whatsapp_cloud' CHECK (channel IN ('whatsapp_cloud','simulator')),
  phone_number_id   text NOT NULL UNIQUE,   -- Meta's ID; how webhooks identify the tenant
  waba_id           text NOT NULL,
  display_phone     text NOT NULL,
  verified_name     text,
  access_token_enc  bytea,                  -- AES-GCM under the tenant data key; NULL for simulator
  coexistence       boolean NOT NULL DEFAULT false,
  status            text NOT NULL DEFAULT 'connected'
                    CHECK (status IN ('connected','disconnected','restricted')),
  quality_rating    text CHECK (quality_rating IN ('green','yellow','red')),
  status_reason     text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, id)
);

-- Owner voice: real example replies used as few-shot examples (FR-CF-7).
CREATE TABLE style_examples (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id    uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  situation      text NOT NULL,
  customer_text  text,
  owner_reply    text NOT NULL,
  source         text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','learned')),
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
-- CATALOG AND PRICING — every price lives on a variant (single source)
-- =====================================================================

CREATE TABLE products (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id  uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  name         text NOT NULL,
  description  text,
  category     text,
  attributes   jsonb NOT NULL DEFAULT '{}',
  active       boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, id)
);
CREATE INDEX products_name_trgm ON products USING gin ((name || ' ' || coalesce(description,'') || ' ' || coalesce(category,'')) gin_trgm_ops);
CREATE INDEX products_biz_idx ON products (business_id, active);

CREATE TABLE product_variants (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id   uuid NOT NULL,
  product_id    uuid NOT NULL,
  name          text NOT NULL DEFAULT 'default',
  attributes    jsonb NOT NULL DEFAULT '{}',
  availability  text NOT NULL DEFAULT 'in_stock'
                CHECK (availability IN ('in_stock','limited','made_to_order','out_of_stock')),
  stock_qty     integer CHECK (stock_qty IS NULL OR stock_qty >= 0),  -- enables honest scarcity (FR-SL-6)
  is_default    boolean NOT NULL DEFAULT false,
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, id),
  FOREIGN KEY (business_id, product_id) REFERENCES products(business_id, id) ON DELETE CASCADE
);
CREATE INDEX variants_product_idx ON product_variants (business_id, product_id);

-- Public pricing policy: safe to show the LLM.
CREATE TABLE pricing_policies (
  variant_id          uuid PRIMARY KEY,
  business_id         uuid NOT NULL,
  disclosure          text NOT NULL
                      CHECK (disclosure IN ('fixed','range','starts_from','after_qualifying','on_request')),
  currency            text NOT NULL DEFAULT 'INR' CHECK (currency ~ '^[A-Z]{3}$'),
  list_price          numeric(12,2) CHECK (list_price IS NULL OR list_price >= 0),
  range_min           numeric(12,2) CHECK (range_min IS NULL OR range_min >= 0),
  range_max           numeric(12,2),
  negotiable          boolean NOT NULL DEFAULT false,
  ai_may_negotiate    boolean NOT NULL DEFAULT false,      -- owner opt-in per item (FR-CF-4)
  concession_steps    integer NOT NULL DEFAULT 0 CHECK (concession_steps >= 0),
  concession_requires jsonb NOT NULL DEFAULT '[]',          -- [{"type":"quantity","min":2},{"type":"advance_payment"},{"type":"repeat_customer"}]
  round_to            numeric(12,2) NOT NULL DEFAULT 1 CHECK (round_to > 0),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, variant_id) REFERENCES product_variants(business_id, id) ON DELETE CASCADE,
  CHECK (disclosure NOT IN ('fixed','starts_from') OR list_price IS NOT NULL),
  CHECK (disclosure <> 'range' OR (range_min IS NOT NULL AND range_max IS NOT NULL AND range_min <= range_max)),
  CHECK (NOT negotiable OR list_price IS NOT NULL),
  CHECK (NOT ai_may_negotiate OR negotiable)
);

-- PRIVATE: floors live in their own table. Only role app_pricing can read it (INV-1).
CREATE TABLE price_floors (
  variant_id   uuid PRIMARY KEY,
  business_id  uuid NOT NULL,
  floor_price  numeric(12,2) NOT NULL CHECK (floor_price >= 0),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, variant_id) REFERENCES product_variants(business_id, id) ON DELETE CASCADE
);

CREATE TABLE offers (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id         uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  variant_id          uuid,                               -- NULL = business-wide offer
  name                text NOT NULL,
  kind                text NOT NULL CHECK (kind IN ('percent','flat','bundle','free_item')),
  value               numeric(12,2) CHECK (value IS NULL OR value >= 0),
  free_item           text,
  conditions          jsonb NOT NULL DEFAULT '{}',        -- min_qty, first_order, payment_mode ...
  starts_at           timestamptz,
  ends_at             timestamptz,                         -- a real end date = honest urgency
  may_cross_floor     boolean NOT NULL DEFAULT false,      -- INV-3: owner explicitly allows this offer below floor
  active              boolean NOT NULL DEFAULT true,
  created_at          timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, variant_id) REFERENCES product_variants(business_id, id) ON DELETE CASCADE,
  CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at),
  CHECK (kind NOT IN ('percent','flat') OR value IS NOT NULL),
  CHECK (kind <> 'percent' OR value <= 100)
);
CREATE UNIQUE INDEX offers_biz_id_uq ON offers (business_id, id);

-- What the LLM context builder reads. No floor price exists here.
CREATE VIEW catalog_public WITH (security_invoker = true) AS
SELECT
  v.business_id, p.id AS product_id, p.name AS product_name, p.description, p.category,
  p.attributes AS product_attributes,
  v.id AS variant_id, v.name AS variant_name, v.attributes, v.availability, v.stock_qty,
  pp.disclosure, pp.currency, pp.list_price, pp.range_min, pp.range_max, pp.negotiable
FROM product_variants v
JOIN products p          ON p.business_id = v.business_id AND p.id = v.product_id
JOIN pricing_policies pp ON pp.business_id = v.business_id AND pp.variant_id = v.id
WHERE p.active AND v.active;

-- =====================================================================
-- CUSTOMERS AND CONVERSATIONS
-- =====================================================================

CREATE TABLE customers (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id    uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  wa_id          text NOT NULL,
  name           text,
  profile        jsonb NOT NULL DEFAULT '{}',    -- learned style: language, script, formality, emoji_rate, typical gaps
  is_personal    boolean NOT NULL DEFAULT false, -- owner's personal contact: AI never replies (FR-CF-9)
  opted_out      boolean NOT NULL DEFAULT false, -- honored always (CR-3)
  opted_out_at   timestamptz,
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, wa_id),
  UNIQUE (business_id, id)
);

CREATE TABLE conversations (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id         uuid NOT NULL,
  customer_id         uuid NOT NULL,
  whatsapp_number_id  uuid NOT NULL,
  state               text NOT NULL DEFAULT 'idle'
                      CHECK (state IN ('idle','listening','deciding','composing','typing')),
  -- Monotonic. Bumped on every inbound customer message; any planned action
  -- carrying an older version is stale and discarded (INV-7).
  version             bigint NOT NULL DEFAULT 0,
  lead_stage          text NOT NULL DEFAULT 'new'
                      CHECK (lead_stage IN ('new','exploring','interested','negotiating','ready_to_buy','won','lost')),
  lost_reason         text,
  qualification       jsonb NOT NULL DEFAULT '{}',
  summary             text,                         -- rolling summary for long conversations
  summary_upto        timestamptz,
  ai_paused_until     timestamptz,                  -- owner replied manually / handoff open (FR-CV-9)
  ai_paused_reason    text,
  selling_stopped     boolean NOT NULL DEFAULT false,   -- "not interested" (FR-SL-8)
  nudge_sent_at       timestamptz,
  last_inbound_at     timestamptz,                  -- drives the 24-hour window (INV-8)
  last_outbound_at    timestamptz,
  last_owner_reply_at timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, id),
  UNIQUE (business_id, customer_id, whatsapp_number_id),
  FOREIGN KEY (business_id, customer_id) REFERENCES customers(business_id, id) ON DELETE CASCADE,
  FOREIGN KEY (business_id, whatsapp_number_id) REFERENCES whatsapp_numbers(business_id, id) ON DELETE CASCADE
);
CREATE INDEX conversations_list_idx ON conversations (business_id, whatsapp_number_id, updated_at DESC);

-- One AI decision cycle (FR-OP-2). Also the source of learning signals.
CREATE TABLE turns (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id           uuid NOT NULL,
  conversation_id       uuid NOT NULL,
  conversation_version  bigint NOT NULL,
  inbound_message_ids   uuid[] NOT NULL DEFAULT '{}',
  status                text NOT NULL DEFAULT 'planned'
                        CHECK (status IN ('planned','sent','cancelled','failed')),
  decision              jsonb NOT NULL DEFAULT '{}',   -- structured plan, engine issued values, check results
  signals               jsonb NOT NULL DEFAULT '{}',   -- end-of-turn waits, outcome, bot-suspicion flag ...
  model                 text,
  prompt_versions       jsonb NOT NULL DEFAULT '{}',
  input_tokens          integer,
  output_tokens         integer,
  cost_micros           bigint,                        -- cost in 1e-6 INR
  latency_ms            integer,
  error                 text,
  created_at            timestamptz NOT NULL DEFAULT now(),
  completed_at          timestamptz,
  UNIQUE (business_id, id),
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations(business_id, id) ON DELETE CASCADE
);
CREATE INDEX turns_conv_idx ON turns (business_id, conversation_id, created_at DESC);

CREATE TABLE messages (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL,
  conversation_id  uuid NOT NULL,
  turn_id          uuid,
  wa_message_id    text UNIQUE,       -- idempotent ingestion: Meta redeliveries collide here (INV-6)
  direction        text NOT NULL CHECK (direction IN ('in','out')),
  sender           text NOT NULL CHECK (sender IN ('customer','ai','owner','system')),
  kind             text NOT NULL DEFAULT 'text'
                   CHECK (kind IN ('text','audio','image','reaction','template','other')),
  body             text,
  payload          jsonb NOT NULL DEFAULT '{}',
  reply_to_wa_id   text,
  status           text NOT NULL DEFAULT 'received'
                   CHECK (status IN ('received','queued','sent','delivered','read','failed')),
  error            text,
  gap_ms           integer,           -- gap since this customer's previous message: learning signal
  answered         boolean NOT NULL DEFAULT false,   -- inbound customer message already covered by a turn
  wa_timestamp     timestamptz,
  sent_at          timestamptz,
  delivered_at     timestamptz,
  read_at          timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations(business_id, id) ON DELETE CASCADE,
  FOREIGN KEY (business_id, turn_id) REFERENCES turns(business_id, id) ON DELETE SET NULL (turn_id)
);
CREATE INDEX messages_conv_time_idx ON messages (business_id, conversation_id, created_at);
CREATE INDEX messages_unanswered_idx ON messages (business_id, conversation_id) WHERE direction = 'in' AND NOT answered;

-- =====================================================================
-- SALES OUTCOMES
-- =====================================================================

CREATE TABLE negotiations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL,
  conversation_id  uuid NOT NULL,
  variant_id       uuid NOT NULL,
  quantity         integer NOT NULL DEFAULT 1 CHECK (quantity > 0),
  current_offer    numeric(12,2),
  customer_best    numeric(12,2),     -- highest the customer has offered (never offer less)
  step             integer NOT NULL DEFAULT 0,
  held_below_floor boolean NOT NULL DEFAULT false,   -- "hold once, then hand off"
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open','agreed','declined','handed_off')),
  history          jsonb NOT NULL DEFAULT '[]',
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations(business_id, id) ON DELETE CASCADE,
  FOREIGN KEY (business_id, variant_id) REFERENCES product_variants(business_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX negotiations_one_open_idx
  ON negotiations (business_id, conversation_id, variant_id) WHERE status = 'open';

CREATE TABLE deals (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL,
  conversation_id  uuid NOT NULL,
  kind             text NOT NULL CHECK (kind IN ('order','visit')),
  items            jsonb NOT NULL DEFAULT '[]',
  details          jsonb NOT NULL DEFAULT '{}',
  value            numeric(12,2),
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','won','lost')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  closed_at        timestamptz,
  closed_by        uuid,
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations(business_id, id) ON DELETE CASCADE
);
CREATE INDEX deals_biz_idx ON deals (business_id, status, created_at DESC);

CREATE TABLE handoffs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL,
  conversation_id  uuid NOT NULL,
  reason           text NOT NULL
                   CHECK (reason IN ('unknown_answer','on_request_price','below_floor',
                                     'customer_asked_human','complaint','high_value','system_failure','other')),
  note             text,
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  resolved_at      timestamptz,
  resolved_by      uuid,
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations(business_id, id) ON DELETE CASCADE
);
CREATE INDEX handoffs_open_idx ON handoffs (business_id, status, created_at DESC);

CREATE TABLE knowledge_gaps (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  conversation_id  uuid,
  question         text NOT NULL,
  answer           text,
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','dismissed')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  answered_at      timestamptz,
  FOREIGN KEY (business_id, conversation_id) REFERENCES conversations(business_id, id) ON DELETE SET NULL (conversation_id)
);

-- =====================================================================
-- OWNER CHANNEL (the owner talks to the platform from their own number)
-- =====================================================================

CREATE TABLE owner_messages (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL,
  business_user_id uuid NOT NULL,
  direction        text NOT NULL CHECK (direction IN ('in','out')),
  kind             text NOT NULL DEFAULT 'text' CHECK (kind IN ('text','template','audio','other')),
  purpose          text NOT NULL DEFAULT 'chat'
                   CHECK (purpose IN ('chat','handoff_alert','daily_summary','deal_alert','disconnect_alert','setup','otp')),
  template_name    text,
  body             text,
  wa_message_id    text UNIQUE,
  status           text NOT NULL DEFAULT 'received'
                   CHECK (status IN ('received','queued','sent','delivered','read','failed')),
  error            text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  sent_at          timestamptz,
  FOREIGN KEY (business_id, business_user_id) REFERENCES business_users(business_id, id) ON DELETE CASCADE
);
CREATE INDEX owner_messages_idx ON owner_messages (business_id, business_user_id, created_at DESC);

-- Owner configuration by chat: extracted changes wait for read-back confirmation (FR-CF-8).
CREATE TABLE config_proposals (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id      uuid NOT NULL,
  business_user_id uuid NOT NULL,
  summary          text NOT NULL,
  changes          jsonb NOT NULL,
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','applied','cancelled','expired')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  decided_at       timestamptz,
  FOREIGN KEY (business_id, business_user_id) REFERENCES business_users(business_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX config_proposals_one_pending ON config_proposals (business_id, business_user_id) WHERE status = 'pending';

-- Settings and pricing changes: actor + time. Floors are recorded as changed, never with a value (INV-1).
CREATE TABLE audit_log (
  id                bigserial PRIMARY KEY,
  business_id       uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  actor_account_id  uuid,
  actor_label       text NOT NULL,
  action            text NOT NULL,
  entity            text NOT NULL,
  entity_id         text,
  detail            jsonb NOT NULL DEFAULT '{}',
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_biz_idx ON audit_log (business_id, created_at DESC);

-- =====================================================================
-- AUTH SESSIONS (platform table: refresh tokens rotate; reuse revokes the family)
-- =====================================================================

CREATE TABLE auth_sessions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id    uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  business_id   uuid REFERENCES businesses(id) ON DELETE CASCADE,
  family_id     uuid NOT NULL,
  refresh_hash  text NOT NULL UNIQUE,
  device_label  text,
  ip            text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  revoked_at    timestamptz,
  replaced_by   uuid
);
CREATE INDEX auth_sessions_account_idx ON auth_sessions (account_id, revoked_at);
CREATE INDEX auth_sessions_family_idx ON auth_sessions (family_id);

-- =====================================================================
-- PLATFORM TABLES (accessed by app_system; app_user can only INSERT outbox)
-- =====================================================================

-- Raw webhook log: written before the tenant is known. Replayable (FR-OP-3).
CREATE TABLE webhook_events (
  id               bigserial PRIMARY KEY,
  received_at      timestamptz NOT NULL DEFAULT now(),
  source           text NOT NULL DEFAULT 'whatsapp_cloud',
  signature_valid  boolean NOT NULL,
  payload          jsonb NOT NULL,
  processed_at     timestamptz,
  error            text
);
CREATE INDEX webhook_events_recv_idx ON webhook_events (received_at);

-- Transactional outbox: state change + event in one transaction (event integrity).
CREATE TABLE outbox (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id     uuid,                      -- NULL for platform events (webhook.received)
  event_type      text NOT NULL,
  schema_version  integer NOT NULL DEFAULT 1,
  ordering_key    text,
  entity_key      text,                      -- supersession scope, e.g. "conversation:<id>"
  entity_version  bigint,
  run_at          timestamptz NOT NULL DEFAULT now(),
  payload         jsonb NOT NULL DEFAULT '{}',
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  published_at    timestamptz,
  seq             bigserial
);
CREATE INDEX outbox_unpublished_idx ON outbox (seq) WHERE published_at IS NULL;

-- Queue (v1 backend = Postgres). Same interface can be backed by Redis Streams.
CREATE TABLE jobs (
  id              bigserial PRIMARY KEY,
  queue           text NOT NULL,
  kind            text NOT NULL,
  business_id     uuid,
  ordering_key    text,
  entity_key      text,
  entity_version  bigint,
  dedupe_key      text,
  payload         jsonb NOT NULL DEFAULT '{}',
  run_at          timestamptz NOT NULL DEFAULT now(),
  status          text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','done','cancelled','dead')),
  attempts        integer NOT NULL DEFAULT 0,
  max_attempts    integer NOT NULL DEFAULT 5,
  last_error      text,
  locked_by       text,
  lease_until     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);
CREATE UNIQUE INDEX jobs_dedupe_uq ON jobs (queue, dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX jobs_due_idx      ON jobs (queue, run_at) WHERE status = 'pending';
CREATE INDEX jobs_running_idx  ON jobs (queue, ordering_key) WHERE status = 'running';
CREATE INDEX jobs_entity_idx   ON jobs (entity_key) WHERE status = 'pending';
CREATE INDEX jobs_orderkey_idx ON jobs (queue, ordering_key, run_at, id) WHERE status = 'pending' AND ordering_key IS NOT NULL;
CREATE INDEX jobs_lease_idx    ON jobs (lease_until) WHERE status = 'running';
CREATE INDEX jobs_dead_idx     ON jobs (queue, finished_at) WHERE status = 'dead';

-- Token buckets for per-number outbound rate limits (Meta throughput).
CREATE TABLE rate_buckets (
  key         text PRIMARY KEY,
  tokens      double precision NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Operator-facing alerts (disconnections, dead letters, quality drops).
CREATE TABLE operator_alerts (
  id           bigserial PRIMARY KEY,
  business_id  uuid REFERENCES businesses(id) ON DELETE CASCADE,
  severity     text NOT NULL DEFAULT 'warning' CHECK (severity IN ('info','warning','critical')),
  kind         text NOT NULL,
  message      text NOT NULL,
  detail       jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz
);

-- The simulated WhatsApp network (what a customer's phone would show). Stands in for Meta.
CREATE TABLE sim_messages (
  id               bigserial PRIMARY KEY,
  phone            text NOT NULL,            -- the human's number (customer or owner)
  business_phone   text NOT NULL,            -- the business number it is talking to
  direction        text NOT NULL CHECK (direction IN ('to_user','from_user')),
  kind             text NOT NULL DEFAULT 'text',
  body             text,
  template_name    text,
  wa_message_id    text UNIQUE,
  reaction_to      text,
  status           text NOT NULL DEFAULT 'sent',
  typing           boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sim_messages_phone_idx ON sim_messages (phone, business_phone, id);

-- =====================================================================
-- updated_at triggers
-- =====================================================================
CREATE TRIGGER businesses_updated    BEFORE UPDATE ON businesses       FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER wa_numbers_updated    BEFORE UPDATE ON whatsapp_numbers FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER products_updated      BEFORE UPDATE ON products         FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER pricing_updated       BEFORE UPDATE ON pricing_policies FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER floors_updated        BEFORE UPDATE ON price_floors     FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER conversations_updated BEFORE UPDATE ON conversations    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER negotiations_updated  BEFORE UPDATE ON negotiations     FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =====================================================================
-- ROW-LEVEL SECURITY
-- =====================================================================
ALTER TABLE businesses ENABLE ROW LEVEL SECURITY;
ALTER TABLE businesses FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON businesses
  USING (id = current_business_id()) WITH CHECK (id = current_business_id());

SELECT enable_tenant_rls(t::regclass) FROM unnest(ARRAY[
  'business_users','whatsapp_numbers','style_examples',
  'products','product_variants','pricing_policies','price_floors','offers',
  'customers','conversations','turns','messages',
  'negotiations','deals','handoffs','knowledge_gaps',
  'owner_messages','config_proposals','audit_log'
]) AS t;

-- A tenant may read the accounts (phone, name) of its own members only.
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY member_read ON accounts FOR SELECT
  USING (EXISTS (SELECT 1 FROM business_users bu
                 WHERE bu.account_id = accounts.id AND bu.business_id = current_business_id()));

-- Tenant code may only INSERT outbox rows for its own tenant; it can never read or edit them.
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_insert ON outbox FOR INSERT WITH CHECK (business_id = current_business_id());

-- =====================================================================
-- FLOOR PRICE ACCESS (INV-1)
-- app_user has NO privileges on price_floors. It writes through these
-- functions and can only ask whether a floor exists, never its value.
-- =====================================================================
CREATE FUNCTION set_price_floor(p_variant uuid, p_floor numeric) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF current_business_id() IS NULL THEN RAISE EXCEPTION 'no tenant context'; END IF;
  INSERT INTO price_floors (variant_id, business_id, floor_price)
  VALUES (p_variant, current_business_id(), p_floor)
  ON CONFLICT (variant_id) DO UPDATE SET floor_price = EXCLUDED.floor_price, updated_at = now();
END $$;

CREATE FUNCTION clear_price_floor(p_variant uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF current_business_id() IS NULL THEN RAISE EXCEPTION 'no tenant context'; END IF;
  DELETE FROM price_floors WHERE variant_id = p_variant AND business_id = current_business_id();
END $$;

CREATE FUNCTION price_floor_is_set(p_variant uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path = public AS $$
BEGIN
  IF current_business_id() IS NULL THEN RAISE EXCEPTION 'no tenant context'; END IF;
  RETURN EXISTS (SELECT 1 FROM price_floors WHERE variant_id = p_variant AND business_id = current_business_id());
END $$;

REVOKE ALL ON FUNCTION set_price_floor(uuid, numeric), clear_price_floor(uuid), price_floor_is_set(uuid) FROM PUBLIC;
