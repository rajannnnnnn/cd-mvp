-- 0011 — prices must be above zero. The checks apply to every new or changed row. They are added NOT VALID so that a database which already
-- holds a zero price still migrates; find those rows with the query below, correct them, then run VALIDATE CONSTRAINT on each.
--   SELECT 'pricing_policies', variant_id FROM pricing_policies WHERE list_price = 0 OR range_min = 0 OR range_max = 0
--   UNION ALL SELECT 'price_floors', variant_id FROM price_floors WHERE floor_price = 0
--   UNION ALL SELECT 'offers', id FROM offers WHERE value = 0;
ALTER TABLE pricing_policies ADD CONSTRAINT pricing_policies_list_price_positive CHECK (list_price IS NULL OR list_price > 0) NOT VALID;
ALTER TABLE pricing_policies ADD CONSTRAINT pricing_policies_range_min_positive  CHECK (range_min IS NULL OR range_min > 0) NOT VALID;
ALTER TABLE pricing_policies ADD CONSTRAINT pricing_policies_range_max_positive  CHECK (range_max IS NULL OR range_max > 0) NOT VALID;
ALTER TABLE price_floors     ADD CONSTRAINT price_floors_floor_positive          CHECK (floor_price > 0) NOT VALID;
ALTER TABLE offers           ADD CONSTRAINT offers_value_positive                CHECK (value IS NULL OR value > 0) NOT VALID;
