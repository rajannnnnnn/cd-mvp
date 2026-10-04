-- 0003 — the public catalog view also exposes which variant is the default and how many variants a product has
-- (used to quote the default variant and to mention alternatives). Still no floor price anywhere in this view.
CREATE OR REPLACE VIEW catalog_public WITH (security_invoker = true) AS
SELECT
  v.business_id, p.id AS product_id, p.name AS product_name, p.description, p.category,
  p.attributes AS product_attributes,
  v.id AS variant_id, v.name AS variant_name, v.attributes, v.availability, v.stock_qty,
  pp.disclosure, pp.currency, pp.list_price, pp.range_min, pp.range_max, pp.negotiable,
  v.is_default,
  (SELECT count(*) FROM product_variants x WHERE x.product_id = p.id AND x.business_id = p.business_id AND x.active)::int AS variant_count
FROM product_variants v
JOIN products p          ON p.business_id = v.business_id AND p.id = v.product_id
JOIN pricing_policies pp ON pp.business_id = v.business_id AND pp.variant_id = v.id
WHERE p.active AND v.active;
