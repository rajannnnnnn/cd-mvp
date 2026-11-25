"""Plain-language reports for configuration and start-up problems, so nobody has to read a stack trace to learn what to fix.

`explain` turns the exception that stopped a part into a short message: what is missing or wrong, what is required, where to fix it.
`advisories` lists settings that are optional but worth setting; they never stop a part from starting."""
from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

_NAME = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b")

HOW = ("Fix it in this part's .env file under deploy/<part>/ (the template .env.example next to it lists every setting), then start the part again.\n"
       "To check everything at once run:  python -m salesai doctor <role>   (role: api, worker, ingress, admin, scheduler, migrate)")


def _validation_lines(exc: ValidationError) -> list[str]:
    lines, seen = [], set()
    for e in exc.errors():
        loc = str(e["loc"][0]).upper() if e.get("loc") else ""
        msg = str(e.get("msg", "")).removeprefix("Value error, ").strip()
        if e.get("type") == "missing":
            line = f"{loc}: not set"
        elif e.get("type") == "literal_error" and loc == "ROLE":
            line = "ROLE: not a known role (use api, ingress, web, admin, worker, scheduler, migrate or all)"
        elif re.fullmatch(r"[A-Z0-9_, ]+ (?:is|are) required for role '.*'", msg):
            for n in re.split(r",\s*", msg.split(" is required")[0].split(" are required")[0]):
                line = f"{n}: not set (required for this part)"
                if line not in seen:
                    seen.add(line)
                    lines.append(line)
            continue
        else:
            name = loc or (_NAME.search(msg).group(1) if _NAME.search(msg) else "")   # type: ignore[union-attr]
            line = f"{name}: {msg}" if name and not msg.startswith(name) else msg
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def explain(exc: BaseException, role: str = "") -> str:
    """The message to print when a part cannot start. Never a traceback."""
    who = f'the "{role}" part' if role else "this part"
    if isinstance(exc, ValidationError):
        lines = _validation_lines(exc)
        body = "\n".join(f"  - {x}" for x in lines)
        return (f"Cannot start {who}: {len(lines)} required setting(s) are missing or not valid.\n\n"
                f"Required (it will not start without these):\n{body}\n\n{HOW}")
    name = type(exc).__name__
    text = str(exc).splitlines()[0][:200] if str(exc) else ""
    if name in ("OperationalError", "PoolTimeout", "ConnectionRefusedError", "ConnectionError", "TimeoutError", "gaierror") or "connection" in text.lower():
        return (f"Cannot start {who}: the database is not reachable ({name}).\n"
                "Check that Postgres is running (docker ps), and DATABASE_URL, SYSTEM_DATABASE_URL and MIGRATION_DATABASE_URL: host, port, user, password.\n"
                "From inside Docker the host is usually host.docker.internal, not localhost.\n" + HOW)
    if name == "FileNotFoundError" and "migrations" in text.lower():
        return f"Cannot start {who}: the migration files were not found ({text}).\nThe image may be broken: rebuild it with scripts/stack.ps1 db -Build."
    return (f"Cannot start {who}: unexpected problem: {name}: {text}\n"
            "Start again with LOG_LEVEL=DEBUG to see the full details, then run:  python -m salesai doctor <role>")


def advisories(s: Any) -> list[str]:
    """Optional settings that are not set. Printed as warnings; they never block a start."""
    out: list[str] = []
    if s.role in ("api", "scheduler", "all", "web") and not s.alert_emails:
        out.append("ALERT_EMAIL_TO (and SMTP_HOST or SES_REGION) are not set: platform alerts will appear in the console only, not by email.")
    if s.role in ("api", "all", "web") and str(getattr(s, "invoice_seller_name", "")).startswith("["):
        out.append("INVOICE_SELLER_NAME / ADDRESS / GSTIN still hold the placeholder text: invoices will show it.")
    if getattr(s, "uses_model", False) and all(s.llm_stage_provider(st) == "local" for st in ("planner", "writer", "check")):
        out.append("LLM_PROVIDER is the local stand-in: replies are rule-based, not from a real model.")
    return out
