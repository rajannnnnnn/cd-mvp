-- 0007 — an offer can apply to a whole category (e.g. every saree) instead of one variant. NULL on both = the whole shop.
ALTER TABLE offers ADD COLUMN category text;
ALTER TABLE offers ADD CONSTRAINT offers_one_target CHECK (variant_id IS NULL OR category IS NULL);
