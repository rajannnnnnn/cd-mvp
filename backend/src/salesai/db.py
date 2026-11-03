"""Database access: four pools, one per DB role. Tenant work always runs inside a transaction
that first sets app.business_id, so Postgres Row-Level Security applies (INV-5)."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

Conn = AsyncConnection[DictRow]


def jsonb(value: object) -> Jsonb:
    return Jsonb(value)


class Database:
    """Pools: `user` (RLS), `pricing` (floor reader), `system` (BYPASSRLS, platform only)."""

    def __init__(self, user_url: str, pricing_url: str | None, system_url: str, pool_max: int = 10, console_url: str | None = None):
        def mk(url: str, name: str, maxsize: int) -> AsyncConnectionPool[Conn]:
            return AsyncConnectionPool(
                url, min_size=1, max_size=maxsize, open=False, name=name,
                kwargs={"row_factory": dict_row, "autocommit": True},
            )

        self.user = mk(user_url, "user", pool_max)
        self.pricing = mk(pricing_url, "pricing", max(2, pool_max // 2)) if pricing_url else None      # only the agent's turn worker has this login (INV-1)
        self.system = mk(system_url, "system", max(2, pool_max // 2))
        self.console = mk(console_url, "console", max(2, pool_max // 2)) if console_url else None      # the founder console only (ADR 0043)

    def _pools(self) -> list[AsyncConnectionPool[Conn]]:
        return [p for p in (self.user, self.pricing, self.system, self.console) if p is not None]

    async def open(self) -> None:
        for p in self._pools():
            await p.open(wait=True, timeout=15)

    async def close(self) -> None:
        for p in self._pools():
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
        if self.pricing is None:
            raise RuntimeError("this process has no pricing database login (PRICING_DATABASE_URL): price floors are read only by the agent's turn worker")
        async with self.pricing.connection() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.business_id', %s, true)", (str(business_id),))
            yield conn

    @asynccontextmanager
    async def system_tx(self) -> AsyncIterator[Conn]:
        """Transaction as app_system. Platform work only: tenant resolution, queue, auth."""
        async with self.system.connection() as conn, conn.transaction():
            yield conn

    @asynccontextmanager
    async def console_tx(self) -> AsyncIterator[Conn]:
        """Transaction as app_console: the founder console's own login, the only one that can read admin sign-in secrets, sessions and break-glass grants."""
        if self.console is None:
            raise RuntimeError("this process has no console database login (CONSOLE_DATABASE_URL): only the founder console holds it")
        async with self.console.connection() as conn, conn.transaction():
            yield conn

    async def ping(self) -> bool:
        try:
            for p in self._pools():
                async with p.connection() as c:
                    await c.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False


def required(row: dict[str, Any] | None, what: str = "row") -> dict[str, Any]:
    """A row that must exist (a foreign key or an earlier step guarantees it). A missing row raises LookupError,
    which the worker turns into a retry / dead letter / alert (INV-12) instead of a TypeError on None."""
    if row is None:
        raise LookupError(f"{what} not found")
    return row


# ------------------------------------------------------------------ checks for `python -m salesai doctor` (kept here: they name the floor and console logins, INV-1)
async def check_logins(s: Any) -> tuple[bool, str, str]:
    """Connect with every configured login. Returns (ok, detail, hint)."""
    urls = [("DATABASE_URL", s.database_url), ("SYSTEM_DATABASE_URL", s.system_database_url), ("MIGRATION_DATABASE_URL", s.migration_database_url),
            ("PRICING_DATABASE_URL", s.pricing_database_url), ("CONSOLE_DATABASE_URL", s.console_database_url)]
    seen = []
    for var, url in urls:
        if not url:
            continue
        try:
            async with await psycopg.AsyncConnection.connect(url, connect_timeout=5, autocommit=True) as c:
                await c.execute("SELECT 1")
        except Exception as e:  # noqa: BLE001
            return False, f"{var} did not connect: {type(e).__name__}", f"Check {var}: host, port, database name, and the login's password."
        seen.append("shop" if var == "DATABASE_URL" else var.removesuffix("_DATABASE_URL").lower())
    return True, f"connected as {', '.join(seen)}", ""


async def _refused(url: str, sql: str) -> bool:
    try:
        async with await psycopg.AsyncConnection.connect(url, connect_timeout=5, autocommit=True) as c:
            await c.execute(sql)
    except psycopg.errors.InsufficientPrivilege:
        return True
    except psycopg.Error:
        return False
    return False


async def check_separation(s: Any) -> list[str]:
    """What is wrong with the separation of logins: the shop's login must not read a floor, the shared platform login must not read the console's secrets."""
    problems = []
    if not await _refused(s.database_url, "SELECT 1 FROM price_floors LIMIT 1"):
        problems.append("DATABASE_URL's login can read price floors (INV-1)")
    if not await _refused(s.system_database_url, "SELECT 1 FROM admin_credentials LIMIT 1"):
        problems.append("SYSTEM_DATABASE_URL's login can read the console's sign-in secrets")
    return problems
