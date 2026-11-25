"""Process entrypoint. One build artifact, several roles selected by configuration:
    python -m salesai [api|ingress|web|admin|worker|scheduler|migrate|all]
    python -m salesai admin-enroll [--new-secret]  (show the founder's authenticator as a QR code, or make a new secret for the settings; see docs/ADMIN_CONSOLE.md)
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


def _print_enrolment(secret: str, account: str, *, headline: str) -> None:
    import sys

    from salesai.modules.auth import totp
    from salesai.modules.auth.admin import ISSUER
    from salesai.qr import printable_qr
    uri = totp.provisioning_uri(secret, account, ISSUER)
    NL = chr(10)
    print(f"{NL}{headline}{NL}Scan this with an authenticator app (Google Authenticator, Authy, 1Password...):{NL}")  # noqa: T201
    print(printable_qr(uri, sys.stdout.encoding))  # noqa: T201
    print(f"Cannot scan? Type this key in instead (time-based, 6 digits):  {secret}{NL}{uri}{NL}")  # noqa: T201


def admin_new_secret() -> None:
    """Make a fresh authenticator secret to put in the deployment's settings (ADR 0056). It is printed once and stored nowhere: whoever holds the `.env`
    holds it. Needs no database and no other setting."""
    import os

    from salesai.modules.auth import totp
    secret = totp.new_secret()
    who = os.environ.get("FOUNDER_EMAIL") or os.environ.get("FOUNDER_PHONE") or "founder"
    _print_enrolment(secret, who, headline="A new authenticator secret. It is stored nowhere: put it in deploy/admin/.env and restart the console.")
    NL = chr(10)
    print(f"Put this line in deploy/admin/.env:{NL}FOUNDER_TOTP_SECRET={secret}{NL}Keep a copy of the key in a password manager or print it and lock it away: with it, a lost phone is not a lost console.{NL}")  # noqa: T201


async def admin_enroll() -> None:
    """Run on the server (ADR 0056). Shows the QR code for the authenticator secret in the deployment's settings, to add to a phone, and starts the founder's
    sign-in state afresh: every console session ends and every trusted browser is forgotten. The secret, the number and the email are settings; this
    command changes none of them and stores nothing secret. `--new-secret` makes a new secret to put in the settings (nothing else happens)."""
    from salesai.modules.auth.admin import enroll_admin
    from salesai.runtime import Runtime

    s = get_settings()
    rt = await Runtime.create(s)
    try:
        secret = await enroll_admin(rt.db, s)
    finally:
        await rt.close()
    _print_enrolment(secret, s.founder_email or s.founder_phone, headline=f"The authenticator for the founder ({s.founder_phone}, {s.founder_email}).")
    print("The next sign-in from any browser is a first one: it asks for a WhatsApp code and an email code too. Every console session was signed out.")  # noqa: T201


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
    from salesai.logsummary import startup_line
    log.info("%s", startup_line(s, role))
    await stop.wait()
    for sv in servers:
        sv.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)
    await rt.close()


def _dispatch(args: list[str]) -> int:
    """Runs the command; returns the exit code. The role comes from the command (api, worker, migrate ...), so settings are checked for that role."""
    import os
    if args[:1] == ["admin-enroll"]:
        if args[1:] == ["--new-secret"]:
            admin_new_secret()
            return 0
        if len(args) != 1:
            print("Usage: python -m salesai admin-enroll [--new-secret]   (the founder's number, email and authenticator secret are settings; nothing is typed)")
            return 2
        os.environ["ROLE"] = "admin"
        asyncio.run(admin_enroll())
        return 0
    if args[:1] == ["mail-preview"]:
        from pathlib import Path

        from salesai.mailpreview import build
        files = build(Path(args[1] if len(args) > 1 else "mail-preview"))
        print(f"Wrote {len(files)} files. Open {files[-1]} in a browser.")
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
