-- 0005 — self-serve SaaS foundation: public business slug (cool URLs), onboarding progress, signup attribution.
ALTER TABLE businesses
  ADD COLUMN slug          text,
  ADD COLUMN onboarding    jsonb NOT NULL DEFAULT '{}',        -- steps the owner chose to skip, completion time
  ADD COLUMN signup_source text;
ALTER TABLE accounts ADD COLUMN signup_source text;

-- backfill slugs for businesses that already exist: name -> lowercase words joined by '-', unique by numeric suffix.
-- (the table is FORCE ROW LEVEL SECURITY; the owner role sees no rows unless that is lifted for this one statement)
ALTER TABLE businesses NO FORCE ROW LEVEL SECURITY;
WITH base AS (
  SELECT id, created_at,
         COALESCE(NULLIF(left(trim(both '-' from lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g'))), 34), ''), 'shop') AS s
  FROM businesses),
ranked AS (SELECT id, s, row_number() OVER (PARTITION BY s ORDER BY created_at, id) AS rn FROM base)
UPDATE businesses b SET slug = CASE WHEN r.rn = 1 THEN r.s ELSE r.s || '-' || r.rn END FROM ranked r WHERE r.id = b.id;
ALTER TABLE businesses FORCE ROW LEVEL SECURITY;

ALTER TABLE businesses ALTER COLUMN slug SET NOT NULL;
-- direct inserts (tests, imports) that do not choose a slug get a unique, valid placeholder; the application always chooses one from the name
ALTER TABLE businesses ALTER COLUMN slug SET DEFAULT 'b-' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 12);
ALTER TABLE businesses ADD CONSTRAINT businesses_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$');
CREATE UNIQUE INDEX businesses_slug_uq ON businesses (slug);
