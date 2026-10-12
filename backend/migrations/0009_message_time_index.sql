-- 0009 — dashboard and analytics read a shop's messages by time range. The existing time indexes cover only outbound messages or are
-- keyed by conversation first, so a range over a busy shop was a scan of its whole history. This one serves any time-range read.
CREATE INDEX messages_biz_time_idx ON messages (business_id, created_at);
