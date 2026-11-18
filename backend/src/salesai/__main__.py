"""Process entrypoint. One build artifact, several roles selected by configuration:
    python -m salesai [api|ingress|web|admin|worker|scheduler|migrate|all]
    python -m salesai admin-enroll     (set up the founder's authenticator, printed as a QR code; see docs/ADMIN_CONSOLE.md)
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


async def admin_enroll() -> None:
    """Run on the server: the only way to set up (or replace) the founder's authenticator (ADR 0047). The founder is FOUNDER_PHONE; nothing is typed.
    Prints a QR code to scan with an authenticator app (and the text secret and link as a fallback); the secret is shown once and never stored
    in the clear. Running it again replaces the authenticator, signs out every console session and forgets every trusted browser."""
    import sys

    from salesai.modules.auth import totp
    from salesai.modules.auth.admin import ISSUER, enroll_admin
    from salesai.qr import printable_qr
    from salesai.runtime import Runtime

    s = get_settings()
    rt = await Runtime.create(s)
    try:
        secret = await enroll_admin(rt.db, s)
    finally:
        await rt.close()
    uri = totp.provisioning_uri(secret, s.founder_phone, ISSUER)
    NL = chr(10)
    print(f"{NL}Authenticator enrolled for the founder ({s.founder_phone}).{NL}Scan this with an authenticator app (Google Authenticator, Authy, 1Password...):{NL}")  # noqa: T201
    print(printable_qr(uri, sys.stdout.encoding))  # noqa: T201
    print(f"Cannot scan? Type this key in instead (time-based, 6 digits):  {secret}{NL}{uri}{NL}")  # noqa: T201
    print("The first sign-in from each browser also asks for a WhatsApp code to this number. Every console session was signed out.")  # noqa: T201


async def run(role: str) -> None:
    s = get_settings()
    setup_logging(s.log_level)
    from salesai.envcheck import advisories
    for note in advisories(s):
        log.warning("setting not set (optional): %s", note)
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
    from salesai.heartbeat import Heartbeat, unit_name
    tasks.append(asyncio.create_task(Heartbeat(rt.db, unit_name(role, s), role, s.heartbeat_every_s).run(stop)))   # ADR 0046
    log.info("salesai role=%s queues=%s backend=%s", role, s.queues if role in ("worker", "all") else "-", s.queue_backend)
    await stop.wait()
    for sv in servers:
        sv.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)
    await rt.close()


def _dispatch(args: list[str]) -> int:
    """Runs the command; returns the exit code. The role comes from the command (api, worker, migrate ...), so settings are checked for that role."""
    import os
    if args[:1] == ["admin-enroll"]:
        if len(args) != 1:
            print("Usage: python -m salesai admin-enroll     (the founder's number is the FOUNDER_PHONE setting; nothing is typed)")
            return 2
        os.environ["ROLE"] = "admin"
        asyncio.run(admin_enroll())
        return 0
    if args[:1] == ["doctor"]:
        from salesai.doctor import render, run_checks
        extra = [a for a in args[1:] if not a.startswith("--")]
        if extra:
            os.environ["ROLE"] = extra[0]
        elif "ROLE" not in os.environ:
            print("Tip: tell the doctor which part to check, e.g.  python -m salesai doctor worker   (api, worker, ingress, admin, scheduler, migrate).\n")
        text, code = render(asyncio.run(run_checks(get_settings(), send_mail="--send-test-mail" in args)))
        print(text)
        return code
    role = args[0] if args else os.environ.get("ROLE", "all")
    os.environ["ROLE"] = role
    asyncio.run(run(role))
    return 0


def main() -> None:
    if sys.platform == "win32":     # psycopg's async mode cannot use Windows' default Proactor event loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    import os

    from salesai.envcheck import explain
    args = sys.argv[1:]
    try:
        code = _dispatch(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        code = 130
    except Exception as e:  # noqa: BLE001 - one place that turns any start-up failure into a plain message and an exit code
        print(explain(e, os.environ.get("ROLE") or (args[0] if args else "")), file=sys.stderr)
        if os.environ.get("LOG_LEVEL", "").upper() == "DEBUG":
            import traceback
            traceback.print_exc()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
