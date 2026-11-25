"""Email to the founder (ADR 0029, 0052): plain SMTP works with any provider, and Amazon SES can be called through its API. The
transport is configuration, so the vendor stays swappable. The recipients are configuration and nothing else: this file knows
no shop, owner or customer address."""
from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any, Literal

from salesai.modules.alerting.format import digest_parts, email_parts


def _one_line(s: str) -> str:
    """Header text must be a single line: anything that came from a shop's name or a failure message is untrusted."""
    return " ".join(str(s).split())


class EmailSink:
    name = "email"
    levels = frozenset({"low", "medium", "high", "critical"})
    digests = True                                # takes the rollups and the daily digest as well as single alerts

    def __init__(self, host: str, port: int, *, sender: str, to: list[str], security: Literal["starttls", "ssl", "none"] = "starttls",
                 username: str = "", password: str = "", timeout_s: float = 15.0,
                 deliver: Callable[[EmailMessage], str | None] | None = None,
                 html_alert: Callable[[dict[str, Any], str], str] | None = None, html_digest: Callable[[str, list[dict[str, Any]]], str] | None = None):
        self.host, self.port, self.sender, self.to = host, port, sender, list(to)
        self.security, self.username, self.password, self.timeout_s = security, username, password, timeout_s
        self.html_alert, self.html_digest = html_alert, html_digest       # the laid-out version of the same words (modules/mail/templates.py)
        self.last_ref: str | None = None            # the provider's id for the last message (SES), for the delivery record
        self._transport = deliver                  # None = SMTP with the settings above; otherwise e.g. ses_deliver(...)

    async def send(self, alert: dict[str, Any], text: str) -> None:       # noqa: ARG002
        subject, body = email_parts(alert)
        await self.send_mail(subject, body, self.html_alert(alert, body) if self.html_alert else None)

    async def send_digest(self, severity: str, alerts: list[dict[str, Any]]) -> None:
        subject, body = digest_parts(severity, alerts)
        await self.send_mail(subject, body, self.html_digest(subject, alerts) if self.html_digest else None)

    async def send_mail(self, subject: str, body: str, html: str | None = None) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = _one_line(subject)[:200], _one_line(self.sender), ", ".join(self.to)
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
        await asyncio.to_thread(self._deliver, msg)

    def _deliver(self, msg: EmailMessage) -> None:
        if self._transport is not None:
            self.last_ref = self._transport(msg)
            return
        self.last_ref = None
        ctx = ssl.create_default_context()
        smtp = (smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout_s, context=ctx) if self.security == "ssl"
                else smtplib.SMTP(self.host, self.port, timeout=self.timeout_s))
        with smtp:
            if self.security == "starttls":
                smtp.starttls(context=ctx)
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(msg, from_addr=self.sender, to_addrs=self.to)


def smtp_check(host: str, port: int, security: str, username: str, password: str, sender: str, to: list[str], *, send: bool, timeout_s: float = 15.0) -> None:
    """For `python -m salesai doctor`: log in to the mail server, and with `send` deliver one test message. Raises on any failure."""
    ctx = ssl.create_default_context()
    smtp = smtplib.SMTP_SSL(host, port, timeout=timeout_s, context=ctx) if security == "ssl" else smtplib.SMTP(host, port, timeout=timeout_s)
    with smtp:
        if security == "starttls":
            smtp.starttls(context=ctx)
        if username:
            smtp.login(username, password)
        if send:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = "ChatDeal test alert", sender, ", ".join(to)
            msg.set_content("This is the test message from `python -m salesai doctor --send-test-mail`. Alerts will arrive at this address.")
            smtp.send_message(msg, from_addr=sender, to_addrs=to)


def _ses_client(region: str, access_key_id: str, secret_access_key: str, timeout_s: float = 15.0) -> Any:
    import boto3  # noqa: PLC0415  loaded here: only a deployment that chooses SES needs it
    from botocore.config import Config  # noqa: PLC0415
    kw: dict[str, Any] = {"aws_access_key_id": access_key_id, "aws_secret_access_key": secret_access_key} if access_key_id else {}
    return boto3.client("ses", region_name=region, config=Config(connect_timeout=timeout_s, read_timeout=timeout_s, retries={"max_attempts": 2}), **kw)


def ses_deliver(region: str, access_key_id: str = "", secret_access_key: str = "", *, client: Any = None) -> Callable[[EmailMessage], str | None]:
    """Send through the Amazon SES API (ADR 0052). With no keys given, boto3 uses its own credential chain (an IAM role on the
    machine), the better choice in production. `client` is for tests."""
    def deliver(msg: EmailMessage) -> str | None:
        plain, rich = msg.get_body(preferencelist=("plain",)), msg.get_body(preferencelist=("html",))
        body: dict[str, Any] = {"Text": {"Data": plain.get_content() if plain is not None else "", "Charset": "UTF-8"}}
        if rich is not None and rich.get_content_type() == "text/html":
            body["Html"] = {"Data": rich.get_content(), "Charset": "UTF-8"}

        def addrs(header: str) -> list[str]:
            return [a.strip() for a in str(msg[header] or "").split(",") if a.strip()]
        dest: dict[str, Any] = {"ToAddresses": addrs("To")}
        if addrs("Bcc"):
            dest["BccAddresses"] = addrs("Bcc")
        extra: dict[str, Any] = {"ReplyToAddresses": addrs("Reply-To")} if addrs("Reply-To") else {}
        reply = (client or _ses_client(region, access_key_id, secret_access_key)).send_email(
            Source=str(msg["From"]), Destination=dest, **extra,
            Message={"Subject": {"Data": str(msg["Subject"]), "Charset": "UTF-8"}, "Body": body})
        return str((reply or {}).get("MessageId") or "") or None
    return deliver


def ses_check(region: str, access_key_id: str, secret_access_key: str, sender: str, to: list[str], *, send: bool, client: Any = None) -> str:
    """For `python -m salesai doctor`: ask SES for the account's send limit (proves the keys and region), and with `send` deliver one
    test message. Returns a short note. Raises on any failure."""
    client = client or _ses_client(region, access_key_id, secret_access_key)
    quota = client.get_send_quota()
    note = f"SES {region} reachable, limit {int(quota.get('Max24HourSend', 0))} per day"
    if send:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = "ChatDeal test alert", sender, ", ".join(to)
        msg.set_content("This is the test message from `python -m salesai doctor --send-test-mail`. Alerts will arrive at this address.")
        ses_deliver(region, client=client)(msg)
    return note


def smtp_deliver(host: str, port: int, security: str, username: str, password: str, timeout_s: float = 15.0) -> Callable[[EmailMessage], str | None]:
    """The same plain-SMTP delivery as `EmailSink`, as a function, for mail to shop owners. Bcc recipients are sent to and not shown."""
    def deliver(msg: EmailMessage) -> str | None:
        ctx = ssl.create_default_context()
        smtp = smtplib.SMTP_SSL(host, port, timeout=timeout_s, context=ctx) if security == "ssl" else smtplib.SMTP(host, port, timeout=timeout_s)
        with smtp:
            if security == "starttls":
                smtp.starttls(context=ctx)
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)
        return None
    return deliver
