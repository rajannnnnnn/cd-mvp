-- Run ONCE per database cluster as a superuser. Passwords come from psql variables
-- (never hard-coded):  psql -v owner_pw=... -v user_pw=... -v system_pw=... -v pricing_pw=... -f bootstrap_roles.sql
--
--   app_owner   owns the schema; used ONLY by the migration job. FORCE RLS still applies to it.
--   app_user    all tenant work (API, workers). Row-Level Security applies. No access to price_floors.
--   app_pricing the pricing component ONLY. Row-Level Security applies. May read price_floors (INV-1).
--   app_system  platform work (webhook tenant resolution, queue, outbox relay, auth). BYPASSRLS.
SELECT format('CREATE ROLE app_owner   LOGIN PASSWORD %L', :'owner_pw')   WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_owner')   \gexec
SELECT format('CREATE ROLE app_user    LOGIN PASSWORD %L', :'user_pw')    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user')    \gexec
SELECT format('CREATE ROLE app_pricing LOGIN PASSWORD %L', :'pricing_pw') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_pricing') \gexec
SELECT format('CREATE ROLE app_system  LOGIN PASSWORD %L BYPASSRLS', :'system_pw') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_system') \gexec

SELECT format('GRANT CREATE, CONNECT ON DATABASE %I TO app_owner', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO app_user, app_pricing, app_system', current_database()) \gexec
GRANT CREATE, USAGE ON SCHEMA public TO app_owner;
GRANT USAGE ON SCHEMA public TO app_user, app_pricing, app_system;
