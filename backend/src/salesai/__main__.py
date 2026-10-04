"""Process entrypoint. One build artifact, several roles selected by configuration:
    python -m salesai [api|ingress|web|worker|scheduler|migrate|all]
Every role exposes /healthz and /readyz; migrations are a separate one-off step."""
from __future__ import annotations

import asyncio
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
        loop.add_signal_handler(sig, stop.set)

    tasks: list[asyncio.Task[None]] = []
    servers: list[uvicorn.Server] = []

    def serve(app: FastAPI) -> None:
        server = uvicorn.Server(uvicorn.Config(app, host=s.http_host, port=s.http_port, log_config=None, access_log=False, proxy_headers=True))
        servers.append(server)
        tasks.append(asyncio.create_task(server.serve()))

    if role in ("api", "ingress", "web", "all"):
        serve(create_app(rt, "web" if role == "all" else role))
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
    role = sys.argv[1] if len(sys.argv) > 1 else get_settings().role
    asyncio.run(run(role))


if __name__ == "__main__":
    main()
