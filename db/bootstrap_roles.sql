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

-- Re-running this script sets the passwords again (rotation), so the passwords you pass are always the ones in force.
SELECT format('ALTER ROLE app_owner PASSWORD %L', :'owner_pw') \gexec
SELECT format('ALTER ROLE app_user PASSWORD %L', :'user_pw') \gexec
SELECT format('ALTER ROLE app_pricing PASSWORD %L', :'pricing_pw') \gexec
SELECT format('ALTER ROLE app_system PASSWORD %L', :'system_pw') \gexec

-- app_console: the founder's console ONLY (ADR 0043). It holds everything app_system holds (it is a member of that role) plus the console's own
-- tables (admin sign-in secrets and sessions, break-glass grants), which no other part can read. Pass -v console_pw=... to give it a login;
-- without it the role exists but cannot log in, and `ALTER ROLE app_console LOGIN PASSWORD '...'` enables it later.
\if :{?console_pw}
SELECT format('CREATE ROLE app_console LOGIN PASSWORD %L BYPASSRLS', :'console_pw') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_console') \gexec
SELECT format('ALTER ROLE app_console LOGIN PASSWORD %L', :'console_pw') WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_console' AND NOT rolcanlogin) \gexec
\else
SELECT 'CREATE ROLE app_console NOLOGIN BYPASSRLS' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_console') \gexec
\endif
GRANT app_system TO app_console;

SELECT format('GRANT CREATE, CONNECT ON DATABASE %I TO app_owner', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO app_user, app_pricing, app_system, app_console', current_database()) \gexec
GRANT CREATE, USAGE ON SCHEMA public TO app_owner;
GRANT USAGE ON SCHEMA public TO app_user, app_pricing, app_system, app_console;
