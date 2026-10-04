"""Test infrastructure: a REAL Postgres (no mocks). A fresh database is created per session,
roles are bootstrapped from db/bootstrap_roles.sql and migrations are applied exactly as in
production. Set TEST_PG_SUPERUSER_URL to point at another server."""
from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[2]
SUPER = os.environ.get("TEST_PG_SUPERUSER_URL", "postgresql://postgres@127.0.0.1:5432/postgres")
PW = {"owner": "owner_pw", "user": "user_pw", "pricing": "pricing_pw", "system": "system_pw"}


def _with_db(url: str, db: str, user: str | None = None, pw: str | None = None) -> str:
    u = psycopg.conninfo.conninfo_to_dict(url)
    u["dbname"] = db
    if user:
        u["user"] = user
        u["password"] = pw
    return psycopg.conninfo.make_conninfo(**u)


@pytest.fixture(scope="session")
def pg_urls() -> dict[str, str]:
    name = f"salesai_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(SUPER, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    subprocess.run(  # noqa: S603
        ["psql", _with_db(SUPER, name), "-v", "ON_ERROR_STOP=1", "-q",  # noqa: S607
         "-v", f"owner_pw={PW['owner']}", "-v", f"user_pw={PW['user']}",
         "-v", f"pricing_pw={PW['pricing']}", "-v", f"system_pw={PW['system']}",
         "-f", str(ROOT / "db" / "bootstrap_roles.sql")],
        check=True, capture_output=True,
    )
    urls = {
        "super": _with_db(SUPER, name),
        "owner": _with_db(SUPER, name, "app_owner", PW["owner"]),
        "user": _with_db(SUPER, name, "app_user", PW["user"]),
        "pricing": _with_db(SUPER, name, "app_pricing", PW["pricing"]),
        "system": _with_db(SUPER, name, "app_system", PW["system"]),
    }
    from salesai.migrate import run_migrations

    run_migrations(urls["owner"])
    yield urls
    with psycopg.connect(SUPER, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def env(pg_urls: dict[str, str]) -> dict[str, str]:
    e = {
        "DATABASE_URL": pg_urls["user"],
        "PRICING_DATABASE_URL": pg_urls["pricing"],
        "SYSTEM_DATABASE_URL": pg_urls["system"],
        "MIGRATION_DATABASE_URL": pg_urls["owner"],
        "MASTER_KEY": base64.b64encode(b"k" * 32).decode(),
        "JWT_SECRET": "test-jwt-secret-0123456789abcdefghij",
        "OTP_SECRET": "test-otp-secret-0123456789abcdefghij",
        "META_APP_SECRET": "test-app-secret",
        "META_VERIFY_TOKEN": "test-verify-token",
        "OTP_MIN_INTERVAL_S": "0",
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        "BLOB_PATH": "/tmp/salesai-test-blobs",  # noqa: S108
    }
    os.environ.update(e)
    from salesai.config import get_settings

    get_settings.cache_clear()
    return e


@pytest_asyncio.fixture(scope="session")
async def db(env: dict[str, str]):  # noqa: ARG001
    from salesai.config import get_settings
    from salesai.db import Database

    s = get_settings()
    database = Database(s.database_url, s.pricing_database_url, s.system_database_url, 10)
    await database.open()
    yield database
    await database.close()


@pytest_asyncio.fixture(scope="session")
async def rt(env: dict[str, str], request: pytest.FixtureRequest):  # noqa: ARG001
    from salesai.config import get_settings
    from salesai.runtime import Runtime
    from tests.world import pg_queue_factory

    factory = pg_queue_factory
    if os.environ.get("TEST_QUEUE_BACKEND") == "redis":      # run the whole async pipeline on the Redis backend
        from salesai.queue.redis_backend import RedisQueue
        url = request.getfixturevalue("redis_url")
        prefix = f"{{salesai:e2e{uuid.uuid4().hex[:6]}}}:"
        factory = lambda _db, _s: RedisQueue(url, prefix=prefix, backoff_base=0.05)  # noqa: E731
    runtime = await Runtime.create(get_settings(), queue_factory=factory)
    yield runtime
    await runtime.close()


@pytest_asyncio.fixture
async def world(rt):
    from tests.world import World
    return World(rt)


# ------------------------------------------------------------------ redis (queue backend under test)
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def redis_url():
    url = os.environ.get("TEST_REDIS_URL")
    if url:
        yield url
        return
    exe = shutil.which("redis-server")
    if exe is None:
        pytest.skip("no redis-server available (set TEST_REDIS_URL)")
    port, tmp = _free_port(), tempfile.mkdtemp(prefix="salesai-redis-")
    proc = subprocess.Popen([exe, "--port", str(port), "--save", "", "--appendonly", "no", "--dir", tmp,  # noqa: S603
                             "--bind", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield f"redis://127.0.0.1:{port}/0"
    proc.terminate()
    proc.wait(timeout=5)
    shutil.rmtree(tmp, ignore_errors=True)




# ------------------------------------------------------------------ HTTP client against the real app
@pytest_asyncio.fixture
async def app(rt):
    from salesai.api.app import create_app

    return create_app(rt, "web")


@pytest_asyncio.fixture
async def client(app):
    ip = ".".join(str(uuid.uuid4().int >> (8 * i) & 255) for i in range(4))      # each test client is its own "IP" for rate limits
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-Forwarded-For": ip}) as c:
        yield c


