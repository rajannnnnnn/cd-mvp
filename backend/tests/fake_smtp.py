"""A local SMTP server standing in for the mail provider (a vendor stand-in that speaks the real wire format, like
`fake_hook.py`). It accepts mail, records it parsed, and can be told to refuse."""
from __future__ import annotations

import asyncio
import base64
import email
from email import policy
from email.message import EmailMessage
from typing import Any


class FakeSmtp:
    def __init__(self, *, advertise_auth: bool = False) -> None:
        self.messages: list[EmailMessage] = []
        self.envelopes: list[dict[str, Any]] = []        # who the server was told to deliver to (the real recipients)
        self.logins: list[tuple[str, str]] = []
        self.refuse = False
        self.advertise_auth = advertise_auth
        self.port = 0
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _client(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        def say(line: str) -> None:
            w.write(line.encode() + b"\r\n")
        say("220 fake-smtp ready")
        sender, rcpts = "", []
        try:
            while True:
                raw = await r.readline()
                if not raw:
                    break
                line = raw.decode().rstrip("\r\n")
                cmd = line.upper()
                if cmd.startswith(("EHLO", "HELO")):
                    if self.advertise_auth:
                        say("250-fake-smtp")
                        say("250 AUTH PLAIN")
                    else:
                        say("250 fake-smtp")
                elif cmd.startswith("AUTH PLAIN"):
                    _, user, pw = base64.b64decode(line.split(" ", 2)[2]).decode().split("\0")
                    self.logins.append((user, pw))
                    say("235 ok")
                elif cmd.startswith("MAIL FROM"):
                    if self.refuse:
                        say("451 try again later")
                        continue
                    sender, rcpts = line.split(":", 1)[1].strip(" <>"), []
                    say("250 ok")
                elif cmd.startswith("RCPT TO"):
                    rcpts.append(line.split(":", 1)[1].strip(" <>"))
                    say("250 ok")
                elif cmd == "DATA":
                    say("354 end with .")
                    body = b""
                    while True:
                        chunk = await r.readline()
                        if chunk in (b".\r\n", b""):
                            break
                        body += chunk[1:] if chunk.startswith(b"..") else chunk
                    self.messages.append(email.message_from_bytes(body, policy=policy.default))   # type: ignore[arg-type]
                    self.envelopes.append({"from": sender, "to": list(rcpts)})
                    say("250 queued")
                elif cmd == "QUIT":
                    say("221 bye")
                    break
                else:
                    say("250 ok")
                await w.drain()
            await w.drain()
        finally:
            w.close()

    def bodies(self) -> list[str]:
        out = []
        for m in self.messages:
            part = m.get_body(preferencelist=("plain",))
            out.append(part.get_content() if part else "")
        return out

    def subjects(self) -> list[str]:
        return [str(m["Subject"]) for m in self.messages]
