-- 0031 — scheduled deletion. An owner who deletes their shop is not erased at once: the request is recorded with the date it becomes final
-- (DELETION_GRACE_DAYS after the request, 60 by default) and the scheduler erases the shop when that date passes. Until then the assistant is paused
-- and renewals stop; cancelling puts both back exactly as they were (the two `*_was` columns remember). One row per shop; the row goes with the shop.
-- A platform table: the shop's own role cannot read or write it (the API reads it through the platform role on the owner's behalf).
CREATE TABLE deletion_requests (
  business_id     uuid PRIMARY KEY REFERENCES businesses (id) ON DELETE CASCADE,
  requested_by    uuid,
  requested_at    timestamptz NOT NULL DEFAULT now(),
  purge_after     timestamptz NOT NULL,
  ai_was_enabled  boolean NOT NULL,
  renews_was      boolean NOT NULL                -- the subscription's cancel_at_period_end before the request (true = it was already ending)
);
CREATE INDEX deletion_requests_due_idx ON deletion_requests (purge_after);
