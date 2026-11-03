-- Re-applied after every migration run (idempotent). Least privilege per role.
-- Tenant tables are protected by RLS for app_user / app_pricing; app_system bypasses RLS by attribute.

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_user, app_pricing, app_system, app_console;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_user, app_pricing, app_system, app_console;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM app_user, app_pricing, app_system, app_console;

-- ---------------------------------------------------------------- app_user (tenant work)
GRANT SELECT, INSERT, UPDATE, DELETE ON
  businesses, business_users, whatsapp_numbers, style_examples,
  products, product_variants, pricing_policies, offers,
  customers, conversations, turns, messages,
  negotiations, deals, handoffs, knowledge_gaps,
  owner_messages, config_proposals, idempotency_keys
TO app_user;
GRANT SELECT, INSERT ON audit_log TO app_user;            -- append-only from tenant code
GRANT SELECT ON accounts TO app_user;                      -- RLS limits it to the tenant's members
GRANT SELECT ON catalog_public TO app_user;
GRANT INSERT ON outbox TO app_user;                        -- never SELECT/UPDATE (event integrity)
GRANT USAGE ON SEQUENCE audit_log_id_seq, outbox_seq_seq TO app_user;
GRANT EXECUTE ON FUNCTION current_business_id(), set_price_floor(uuid, numeric),
  clear_price_floor(uuid), price_floor_is_set(uuid) TO app_user;
-- NOTE: no grant on price_floors for app_user (INV-1).

-- ---------------------------------------------------------------- app_pricing (pricing component only)
GRANT SELECT ON pricing_policies, price_floors, offers, product_variants TO app_pricing;
GRANT SELECT (id, business_id, category) ON products TO app_pricing;      -- only to match category offers; nothing else about a product

GRANT EXECUTE ON FUNCTION current_business_id() TO app_pricing;

-- ---------------------------------------------------------------- app_system (platform work, BYPASSRLS)
GRANT SELECT, INSERT, UPDATE, DELETE ON
  accounts, otp_challenges, auth_sessions, business_keys, webhook_events, outbox, jobs,
  rate_buckets, operator_alerts, sim_messages
TO app_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON businesses TO app_system;   -- onboarding + hard delete
GRANT SELECT ON business_users, whatsapp_numbers, conversations, customers TO app_system;
GRANT UPDATE (status, quality_rating, status_reason) ON whatsapp_numbers TO app_system;
GRANT SELECT ON audit_log TO app_system;
-- operator console aggregates (counts only; no tenant content is returned by operator endpoints)
GRANT SELECT ON turns, messages, handoffs TO app_system;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_system;
GRANT EXECUTE ON FUNCTION current_business_id() TO app_system;

-- ---------------------------------------------------------------- billing (platform writes, tenants read their own rows)
GRANT SELECT ON subscriptions, invoices, payments TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON subscriptions, invoices, payments TO app_system;
GRANT USAGE ON SEQUENCE invoice_number_seq TO app_system;

-- ---------------------------------------------------------------- support tickets (shop writes its own; support = app_system)
GRANT SELECT, INSERT, UPDATE ON support_tickets TO app_user;
GRANT SELECT, INSERT ON support_messages TO app_user;
GRANT USAGE ON SEQUENCE support_messages_id_seq TO app_user, app_system;
GRANT SELECT, INSERT, UPDATE ON support_tickets TO app_system;
GRANT SELECT, INSERT ON support_messages TO app_system;
GRANT SELECT, INSERT, DELETE ON support_attachments TO app_user;   -- DELETE for per-shop retention of resolved tickets (ADR 0030)
GRANT SELECT, INSERT, DELETE ON learning_observations TO app_user;   -- never edited; deleted only by retention or with the shop (ADR 0031)
GRANT SELECT ON support_attachments TO app_system;

-- ---------------------------------------------------------------- platform audit trail (0016, ADR 0030)
GRANT SELECT, INSERT ON platform_audit_log TO app_system;   -- append-only; the shop role has no access at all (ADR 0030)
GRANT SELECT, INSERT, UPDATE, DELETE ON otp_deliveries TO app_system;   -- the web side writes it, the gateway answers it (ADR 0025)
GRANT SELECT, INSERT, UPDATE ON number_connect_requests TO app_user;          -- the web side asks and may give up (ADR 0025)
GRANT SELECT, UPDATE, DELETE ON number_connect_requests TO app_system;        -- the gateway answers; retention clears
GRANT SELECT, INSERT, UPDATE ON channel_events TO app_user;     -- the gateway hands events over, the agent marks them handled (ADR 0025)
GRANT SELECT, DELETE ON channel_events TO app_system;           -- retention removes handled events

-- ---------------------------------------------------------------- admin plane (app_console only; ADR 0028, 0043)
-- The console's sign-in secrets, sessions and break-glass grants are readable by its own login and by no other part's.
GRANT SELECT, INSERT, UPDATE, DELETE ON admin_credentials, admin_sessions TO app_console;
GRANT SELECT, INSERT, UPDATE ON break_glass_grants TO app_console;
-- the boards count deals and sum their value; the customer's order details never leave the shop's own role
GRANT SELECT (id, business_id, conversation_id, kind, value, status, created_at, closed_at) ON deals TO app_system;
-- the recovery sweep looks for work nobody is doing; it reads ids, states and ages, never message text
GRANT SELECT (id, business_id, business_user_id, direction, purpose, status, created_at) ON owner_messages TO app_system;
-- the agent audit (ADR 0037): the agent writes its own shop's runs; the platform reads them as aggregates and never edits them
GRANT SELECT, INSERT, UPDATE ON agent_runs TO app_user;
GRANT SELECT ON agent_runs TO app_system;
-- the agent's clock (ADR 0038): the agent writes its own shop's timers; the platform's scan for due ones reads them, never edits
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_timers TO app_user;
GRANT SELECT ON agent_timers TO app_system;
-- usage notices (ADR 0032): the platform records them as the billing cycle runs; the shop can read what it was told
GRANT SELECT ON usage_notices TO app_user;
GRANT SELECT, INSERT ON usage_notices TO app_system;
