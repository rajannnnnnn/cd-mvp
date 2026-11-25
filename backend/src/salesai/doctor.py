"""`python -m salesai doctor`: the check to run after filling in the configuration and before opening the doors.

Every check says what it found and, when something is wrong, what to change. Nothing here changes data. Checks that need a service the shop has
not set up yet (email, the platform's WhatsApp token) are skipped with the setting to fill in; a model that is not real (the local stand-in) is
reported as such. Exit code 0 when nothing failed, 1 otherwise."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from pydantic import BaseModel

from salesai.config import Settings
from salesai.db import check_logins, check_separation
from salesai.migrate import migrations_dir
from salesai.modules.agent.llm import factory
from salesai.modules.agent.llm.base import LLMRequest
from salesai.modules.alerting.email_sink import ses_check, smtp_check

Status = Literal["ok", "fail", "skip"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    hint: str = ""


class _Ping(BaseModel):
    ok: bool


async def _connect(url: str) -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(url, connect_timeout=5, autocommit=True)


async def _database(s: Settings) -> Check:
    ok, detail, hint = await check_logins(s)
    return Check("database", "ok" if ok else "fail", detail, hint)


async def _migrations(s: Settings) -> Check:
    want = sorted(f.stem for f in migrations_dir().glob("*.sql") if not f.name.startswith("_"))
    try:
        async with await _connect(s.migration_database_url or s.system_database_url) as c:
            rows = await (await c.execute("SELECT version FROM schema_migrations")).fetchall()
    except Exception as e:  # noqa: BLE001
        return Check("migrations", "fail", f"could not read schema_migrations: {type(e).__name__}", "Run the migrations first: python -m salesai migrate (deploy/db).")
    missing = [v for v in want if v not in {r[0] for r in rows}]
    if missing:
        return Check("migrations", "fail", f"{len(missing)} not applied, first: {missing[0]}", "Run the migration job (python -m salesai migrate) before starting any part.")
    return Check("migrations", "ok", f"{len(want)} applied")


async def _separation(s: Settings) -> Check:
    problems = await check_separation(s)
    if problems:
        return Check("separation", "fail", "; ".join(problems), "Re-run db/bootstrap_roles.sql and the migrations as the owner: the grants are applied every time.")
    return Check("separation", "ok", "floors and console secrets are out of reach of the shop and platform logins")


async def _model(s: Settings) -> Check:
    stages = [st for st in factory.STAGES if s.llm_stage_provider(st) != "local"]
    if not stages:
        return Check("model", "skip", "the local stand-in is configured: replies are rule-based, not from a real model",
                     "Set LLM_PROVIDER (openai_compat or anthropic), its API key and base URL in deploy/agent/.env.")
    try:
        llm = factory.make_llm(s)
    except Exception as e:  # noqa: BLE001
        return Check("model", "fail", f"the model settings are not usable: {e}", "Fix LLM_PROVIDER / LLM_MODEL_* / LLM_PRICES (a model needs a price: [input, output, cached, cache-write] USD per 1M tokens).")
    done = []
    for st in stages:
        req = LLMRequest(stage=st, system='Reply with JSON exactly {"ok": true}.', input={"ping": 1}, model=getattr(s, f"llm_model_{st}"),
                         prompt_ref="doctor.v1", max_tokens=60, effort=getattr(s, f"llm_effort_{st}", None))
        try:
            r = await llm.generate(req, _Ping)
        except Exception as e:  # noqa: BLE001
            return Check("model", "fail", f"{st}: {str(e)[:160]}", f"Check LLM_PROVIDER_{st.upper()} / LLM_PROVIDER, the API key, OPENAI_COMPAT_BASE_URL and LLM_MODEL_{st.upper()}.")
        done.append(f"{st} {r.model} {r.latency_ms}ms")
    return Check("model", "ok", "; ".join(done))


async def _email(s: Settings, send: bool) -> Check:
    if s.email_transport == "ses":
        if not (s.alert_emails and s.ses_region and s.alert_email_from):
            return Check("email", "skip", "alert email is not set up", "Fill in ALERT_EMAIL_TO, ALERT_EMAIL_FROM, SES_REGION (and SES_ACCESS_KEY_ID / SES_SECRET_ACCESS_KEY) in deploy/web/.env; or set EMAIL_TRANSPORT=smtp and SMTP_HOST.")
        try:
            note = await asyncio.to_thread(ses_check, s.ses_region, s.ses_access_key_id, s.ses_secret_access_key, s.alert_email_from, s.alert_emails, send=send)
        except Exception as e:  # noqa: BLE001
            return Check("email", "fail", f"{type(e).__name__}: {str(e)[:120]}", "Check SES_REGION (the region where the sender is verified), the access key and secret, and that ALERT_EMAIL_FROM (and in the SES sandbox ALERT_EMAIL_TO) is verified in SES.")
        return Check("email", "ok", note + ("; test message sent to " + ", ".join(s.alert_emails) if send else "; add --send-test-mail to send one"))
    if not (s.alert_emails and s.smtp_host and s.alert_email_from):
        return Check("email", "skip", "alert email is not set up", "Fill in ALERT_EMAIL_TO, ALERT_EMAIL_FROM, SMTP_HOST (and SMTP_USERNAME / SMTP_PASSWORD) in deploy/web/.env.")
    try:
        await asyncio.to_thread(smtp_check, s.smtp_host, s.smtp_port, s.smtp_security, s.smtp_username, s.smtp_password, s.alert_email_from, s.alert_emails, send=send)
    except Exception as e:  # noqa: BLE001
        return Check("email", "fail", f"{type(e).__name__}: {str(e)[:120]}", "Check SMTP_HOST, SMTP_PORT, SMTP_SECURITY (starttls/ssl), the username and password, and that ALERT_EMAIL_FROM is an address the provider allows.")
    return Check("email", "ok", ("test message sent to " if send else "logged in; add --send-test-mail to send one to ") + ", ".join(s.alert_emails))


def _graph_me(s: Settings) -> str:
    url = f"{s.meta_graph_base}/{s.meta_graph_version}/me?access_token={s.platform_wa_access_token}"
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - the base is configuration (https://graph.facebook.com)
        return str(json.loads(r.read()).get("id", "?"))


async def _meta(s: Settings) -> Check:
    if not s.platform_wa_access_token:
        return Check("meta", "skip", "PLATFORM_WA_ACCESS_TOKEN is not set", "Needed for sign-in codes and platform alerts over WhatsApp (OTP_CHANNEL=whatsapp_cloud): deploy/gateway/.env.")
    try:
        who = await asyncio.to_thread(_graph_me, s)
    except urllib.error.HTTPError as e:
        return Check("meta", "fail", f"Meta answered {e.code}", "The platform token is rejected or expired: create a new system-user token in Meta Business settings.")
    except Exception as e:  # noqa: BLE001
        return Check("meta", "fail", f"{type(e).__name__}", "Could not reach Meta: check the server's outbound network and META_GRAPH_BASE.")
    return Check("meta", "ok", f"token accepted (id {who})")


async def run_checks(s: Settings, *, send_mail: bool = False) -> list[Check]:
    return [await _database(s), await _migrations(s), await _separation(s), await _model(s), await _email(s, send_mail), await _meta(s)]


def render(checks: list[Check]) -> tuple[str, int]:
    mark = {"ok": "OK  ", "fail": "FAIL", "skip": "skip"}
    lines = []
    for c in checks:
        lines.append(f"[{mark[c.status]}] {c.name}: {c.detail}")
        if c.hint and c.status != "ok":
            lines.append(f"        -> {c.hint}")
    failed = sum(c.status == "fail" for c in checks)
    lines.append("")
    lines.append("Everything that is set up works." if not failed else f"{failed} check(s) failed: fix them and run the command again.")
    return "\n".join(lines), 1 if failed else 0
