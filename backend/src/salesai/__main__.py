"""Process entrypoint. One build artifact, several roles selected by configuration:
    python -m salesai [api|ingress|web|admin|worker|scheduler|migrate|all]
    python -m salesai admin-enroll +919999900000     (give a platform operator an authenticator; see docs/ADMIN_CONSOLE.md)
Every role exposes /healthz and /readyz; migrations are a separate one-off step."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI

from salesai.config import get_settings
from salesai.obs import setup_logging

if TYPE_CHECKING:
    from salesai.runtime import Runtime

log = logging.getLogger("salesai")


def health_app(rt: Runtime, role: str) -> FastAPI:
    from salesai.api.routes import system
    app = FastAPI(title=f"salesai-{role}", docs_url=None, openapi_url=None)
    app.state.rt = rt
    app.include_router(system.system)
    return app


async def admin_enroll(phone: str) -> None:
    """Run on the server by the founder: the only way to set up (or replace) the console's second factor. The secret is printed
    once and never stored in the clear; scan or type it into an authenticator app."""
    from salesai.modules.auth import totp
    from salesai.modules.auth.admin import ISSUER, enroll_admin
    from salesai.runtime import Runtime

    s = get_settings()
    rt = await Runtime.create(s)
    try:
        secret = await enroll_admin(rt.db, s, phone)
    finally:
        await rt.close()
    print(f"Authenticator enrolled for {phone}.\nSecret (type it into an authenticator app): {secret}\n{totp.provisioning_uri(secret, phone, ISSUER)}")  # noqa: T201


async def run(role: str) -> None:
    s = get_settings()
    setup_logging(s.log_level)
    if role == "migrate":
        from salesai.migrate import run_migrations
        done = run_migrations(s.migration_database_url)
        log.info("migrations applied: %s", done or "none (up to date)")
        return

    from salesai.api.app import create_app
    from salesai.runtime import Runtime
    from salesai.scheduler import Scheduler

    rt = await Runtime.create(s)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):      # Windows event loops cannot register signal handlers; Ctrl+C then ends the process directly
            loop.add_signal_handler(sig, stop.set)

    tasks: list[asyncio.Task[None]] = []
    servers: list[uvicorn.Server] = []

    def serve(app: FastAPI) -> None:
        server = uvicorn.Server(uvicorn.Config(app, host=s.http_host, port=s.http_port, log_config=None, access_log=False, proxy_headers=True))
        servers.append(server)
        tasks.append(asyncio.create_task(server.serve()))

    if role in ("api", "ingress", "web", "admin", "all"):
        serve(create_app(rt, role))
    elif role in ("worker", "scheduler"):
        serve(health_app(rt, role))
    if role in ("worker", "all"):
        runner = rt.worker()
        tasks.append(asyncio.create_task(runner.run(stop)))
    if role in ("scheduler", "all"):
        tasks.append(asyncio.create_task(Scheduler(rt).run(stop)))
    log.info("salesai role=%s queues=%s backend=%s", role, s.queues if role in ("worker", "all") else "-", s.queue_backend)
    await stop.wait()
    for sv in servers:
        sv.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)
    await rt.close()


def main() -> None:
    if sys.platform == "win32":     # psycopg's async mode cannot use Windows' default Proactor event loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if len(sys.argv) == 3 and sys.argv[1] == "admin-enroll":
        asyncio.run(admin_enroll(sys.argv[2]))
        sys.exit(0)
    role = sys.argv[1] if len(sys.argv) > 1 else get_settings().role
    asyncio.run(run(role))


if __name__ == "__main__":
    main()
