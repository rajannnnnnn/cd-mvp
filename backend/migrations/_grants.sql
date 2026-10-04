-- Re-applied after every migration run (idempotent). Least privilege per role.
-- Tenant tables are protected by RLS for app_user / app_pricing; app_system bypasses RLS by attribute.

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_user, app_pricing, app_system;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_user, app_pricing, app_system;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM app_user, app_pricing, app_system;

-- ---------------------------------------------------------------- app_user (tenant work)
GRANT SELECT, INSERT, UPDATE, DELETE ON
  businesses, business_users, whatsapp_numbers, style_examples,
  products, product_variants, pricing_policies, offers,
  customers, conversations, turns, messages,
  negotiations, deals, handoffs, knowledge_gaps,
  owner_messages, config_proposals
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
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_system;
GRANT EXECUTE ON FUNCTION current_business_id() TO app_system;
