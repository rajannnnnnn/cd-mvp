-- 0027 — every foreign key has an index (tests/test_schema_indexes.py): deleting a shop must not scan the grants table.
CREATE INDEX IF NOT EXISTS break_glass_grants_business_idx ON break_glass_grants (business_id);
