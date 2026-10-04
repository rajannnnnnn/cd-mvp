"""Per-number token bucket for outbound sends (Meta throughput: 5 msgs/s for coexistence numbers). State
lives in the database so every sender instance shares one budget; callers Defer instead of blocking."""
from __future__ import annotations

from salesai.db import Database


async def take(db: Database, key: str, *, rate_per_s: float = 5.0, burst: float = 5.0) -> float:
    """Returns 0.0 when a token was taken, else the seconds to wait before the next token."""
    async with db.system_tx() as c:
        got = await (await c.execute(
            """INSERT INTO rate_buckets (key, tokens, updated_at) VALUES (%(k)s, %(b)s - 1, clock_timestamp())
               ON CONFLICT (key) DO UPDATE SET
                 tokens = LEAST(%(b)s, rate_buckets.tokens + EXTRACT(EPOCH FROM clock_timestamp() - rate_buckets.updated_at) * %(r)s) - 1,
                 updated_at = clock_timestamp()
               WHERE LEAST(%(b)s, rate_buckets.tokens + EXTRACT(EPOCH FROM clock_timestamp() - rate_buckets.updated_at) * %(r)s) >= 1
               RETURNING tokens""", {"k": key, "b": burst, "r": rate_per_s})).fetchone()
        if got:
            return 0.0
        row = await (await c.execute(
            "SELECT LEAST(%(b)s, tokens + EXTRACT(EPOCH FROM clock_timestamp() - updated_at) * %(r)s) AS t FROM rate_buckets WHERE key=%(k)s",
            {"k": key, "b": burst, "r": rate_per_s})).fetchone()
        return max(0.05, (1 - float(row["t"])) / rate_per_s)
