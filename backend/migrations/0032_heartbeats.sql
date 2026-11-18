-- 0032 — heartbeats (ADR 0046). Every process records that it is alive under a readable unit name ("gateway receive", "agent worker",
-- "web scheduler"...). Replicas share a unit, so a unit is down only when none of its copies beats. The schedulers read this table to
-- raise part_down alerts. A platform table: no shop data, and the shop's own role cannot read or write it.
CREATE TABLE heartbeats (
  unit          text PRIMARY KEY,
  role          text NOT NULL,                       -- the process role: api, ingress, admin, worker, scheduler, all
  instance      text NOT NULL,                       -- host and process id of the copy that beat last, for the founder's eyes only
  started_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now()
);
