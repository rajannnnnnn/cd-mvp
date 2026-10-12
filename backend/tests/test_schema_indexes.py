"""Every foreign key has an index to support it, unless it is on the short list of small or cold tables below.

A foreign key without an index turns each delete or update of the parent row into a scan of the child table, and each lookup by that column
into a table scan; with millions of messages that is the difference between milliseconds and seconds (found with loadtest/scale_data.py)."""
from __future__ import annotations

import psycopg

# (table, constraint) pairs that may go without: tiny, written rarely, or already covered by the primary key of the same shape.
EXEMPT = {
    ("pricing_policies", "pricing_policies_business_id_variant_id_fkey"),     # primary key is variant_id
    ("price_floors", "price_floors_business_id_variant_id_fkey"),             # primary key is variant_id
    ("payments", "payments_business_id_invoice_id_fkey"),                      # a handful per shop per year
    ("support_tickets", "support_tickets_opened_by_fkey"),                     # only matters when an account is erased
    ("support_messages", "support_messages_author_account_fkey"),              # same
}


def test_every_foreign_key_is_indexed(pg_urls):
    with psycopg.connect(pg_urls["super"]) as c:
        rows = c.execute(
            """SELECT c.conrelid::regclass::text, c.conname FROM pg_constraint c
               WHERE c.contype = 'f' AND NOT EXISTS (
                 SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND (i.indkey::int2[])[0:array_length(c.conkey, 1) - 1] = c.conkey)""").fetchall()
    missing = sorted((t, n) for t, n in rows if (t, n) not in EXEMPT)
    assert not missing, f"foreign keys without a supporting index: {missing}"
