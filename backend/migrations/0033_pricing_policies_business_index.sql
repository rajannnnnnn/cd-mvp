-- 0033 — an index on pricing_policies (business_id, variant_id). Every shop-scoped read of the catalog view joins this table, and row-level
-- security filters it by business_id; without an index each such read scanned the whole table (found at 50 shops / 20,000 variants: the
-- agent's catalog size check cost 24 ms and the Products page 27 ms, both from this scan). The table's own primary key starts with
-- variant_id, so it could not serve a business_id filter; this index also is the one the composite foreign key to product_variants needs.
CREATE INDEX pricing_policies_business_idx ON pricing_policies (business_id, variant_id);
