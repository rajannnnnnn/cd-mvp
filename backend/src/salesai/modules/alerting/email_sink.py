"""Email to the founder over plain SMTP (ADR 0029): any provider that speaks SMTP works, so the vendor stays swappable. The
recipients are configuration and nothing else: this file knows no shop, owner or customer address."""
from __future__ import annotations

import asyncio
import smtplib
import ssl
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
                 username: str = "", password: str = "", timeout_s: float = 15.0):
        self.host, self.port, self.sender, self.to = host, port, sender, list(to)
        self.security, self.username, self.password, self.timeout_s = security, username, password, timeout_s

    async def send(self, alert: dict[str, Any], text: str) -> None:       # noqa: ARG002
        await self.send_mail(*email_parts(alert))

    async def send_digest(self, severity: str, alerts: list[dict[str, Any]]) -> None:
        await self.send_mail(*digest_parts(severity, alerts))

    async def send_mail(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = _one_line(subject)[:200], _one_line(self.sender), ", ".join(self.to)
        msg.set_content(body)
        await asyncio.to_thread(self._deliver, msg)

    def _deliver(self, msg: EmailMessage) -> None:
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
