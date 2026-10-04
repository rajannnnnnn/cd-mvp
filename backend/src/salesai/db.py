"""Database access: four pools, one per DB role. Tenant work always runs inside a transaction
that first sets app.business_id, so Postgres Row-Level Security applies (INV-5)."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

Conn = AsyncConnection


def jsonb(value: object) -> Jsonb:
    return Jsonb(value)


class Database:
    """Pools: `user` (RLS), `pricing` (floor reader), `system` (BYPASSRLS, platform only)."""

    def __init__(self, user_url: str, pricing_url: str, system_url: str, pool_max: int = 10):
        def mk(url: str, name: str, maxsize: int) -> AsyncConnectionPool:
            return AsyncConnectionPool(
                url, min_size=1, max_size=maxsize, open=False, name=name,
                kwargs={"row_factory": dict_row, "autocommit": True},
            )

        self.user = mk(user_url, "user", pool_max)
        self.pricing = mk(pricing_url, "pricing", max(2, pool_max // 2))
        self.system = mk(system_url, "system", max(2, pool_max // 2))

    async def open(self) -> None:
        for p in (self.user, self.pricing, self.system):
            await p.open(wait=True, timeout=15)

    async def close(self) -> None:
        for p in (self.user, self.pricing, self.system):
            await p.close()

    @asynccontextmanager
    async def tenant(self, business_id: uuid.UUID | str) -> AsyncIterator[Conn]:
        """Transaction as app_user scoped to one tenant."""
        async with self.user.connection() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.business_id', %s, true)", (str(business_id),))
            yield conn

    @asynccontextmanager
    async def pricing_tenant(self, business_id: uuid.UUID | str) -> AsyncIterator[Conn]:
        """Transaction as app_pricing (the only role that can read price_floors)."""
        async with self.pricing.connection() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.business_id', %s, true)", (str(business_id),))
            yield conn

    @asynccontextmanager
    async def system_tx(self) -> AsyncIterator[Conn]:
        """Transaction as app_system. Platform work only: tenant resolution, queue, auth."""
        async with self.system.connection() as conn, conn.transaction():
            yield conn

    async def ping(self) -> bool:
        try:
            for p in (self.user, self.pricing, self.system):
                async with p.connection() as c:
                    await c.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False
