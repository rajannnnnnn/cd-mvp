-- 0024 — queue fencing (ADR 0035): a token that goes up at every claim and never down, so a worker that lost its lease (a long pause,
-- a restart) can neither extend nor finish nor kill a job that has since been given to someone else.
ALTER TABLE jobs ADD COLUMN claim_token bigint NOT NULL DEFAULT 0;
