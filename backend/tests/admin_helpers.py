"""Signing in to the admin plane the way the founder does (ADR 0047): an authenticator code, and a WhatsApp code too when the sign-in looks suspicious."""
from __future__ import annotations

import re

from salesai.modules.auth import totp
from salesai.modules.auth.admin import enroll_admin

A1 = "/admin/v1"


async def forget_codes(world) -> None:
    """The founder's number is the same in every test: wipe its WhatsApp-code history so the per-number hourly limit does not carry over (or build up inside one test)."""
    s = world.rt.settings
    async with world.rt.db.system_tx() as c:
        for table in ("otp_challenges", "otp_deliveries"):
            await c.execute(f"DELETE FROM {table} WHERE phone IN (%s, %s)", (s.founder_phone, s.founder_phone.removeprefix("+")))   # noqa: S608


async def make_admin(world) -> tuple[str, str]:
    """The founder (the FOUNDER_PHONE setting) with an authenticator enrolled. Returns (phone, authenticator secret)."""
    s = world.rt.settings
    await forget_codes(world)
    async with world.rt.db.console_tx() as c:                  # a clean console: what an earlier test left (sessions) must not count in this one
        await c.execute("DELETE FROM admin_sessions")
    async with world.rt.db.system_tx() as c:
        await c.execute(r"DELETE FROM operator_alerts WHERE kind LIKE 'admin\_%'")
    return s.founder_phone, await enroll_admin(world.rt.db, s)


def code_for(rt, secret: str, *, steps_ahead: int = 0) -> str:
    return totp.code_at(secret, int(rt.admin_auth.clock() // 30) + steps_ahead)


async def whatsapp_code(client, phone: str) -> str:
    inbox = (await client.get("/api/v1/sim/inbox", params={"phone": phone})).json()
    return re.search(r"\b(\d{6})\b", [m for m in inbox if m["template_name"] == "otp_login"][-1]["body"]).group(1)


async def sign_in(client, rt, secret: str, *, steps_ahead: int = 0, headers: dict | None = None):
    """The first step as the browser sends it: only an authenticator code (the client keeps the trusted-browser cookie between calls)."""
    return await client.post(f"{A1}/auth/sign-in", json={"code": code_for(rt, secret, steps_ahead=steps_ahead)}, headers=headers)


async def email_code(client, phone: str) -> str:
    """The emailed code. In the simulator it lands in the same inbox as the WhatsApp one (nothing is really sent)."""
    inbox = (await client.get("/api/v1/sim/inbox", params={"phone": phone})).json()
    return re.search(r"([0-9]{8})", [m for m in inbox if m["template_name"] == "admin_login_code_email"][-1]["body"]).group(1)


async def step_up(client, phone: str, ticket: str, *, headers: dict | None = None, channels: tuple[str, ...] = ("whatsapp", "email")):
    """The extra proof: for each channel in turn ask for the code, read it where the founder would, send it back. Stops at the first session (an elevated
    sign-in needs one channel, a high one needs both). Returns the last response."""
    last = None
    for channel in channels:
        sent = await client.post(f"{A1}/auth/step-up/send", json={"ticket": ticket, "channel": channel}, headers=headers)
        assert sent.status_code == 202, sent.text
        code = await (whatsapp_code(client, phone) if channel == "whatsapp" else email_code(client, phone))
        last = await client.post(f"{A1}/auth/step-up/verify", json={"ticket": ticket, "code": code, "channel": channel}, headers=headers)
        assert last.status_code == 200, last.text
        if "access_token" in last.json():
            return last
    return last


async def admin_login(client, rt, phone: str, secret: str, *, steps_ahead: int = 0, headers: dict | None = None) -> dict:
    """A full sign-in, with the WhatsApp code when the console asks for it. Returns the session."""
    r = await sign_in(client, rt, secret, steps_ahead=steps_ahead, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    if "step_up" in body:
        v = await step_up(client, phone, body["ticket"], headers=headers)
        assert v.status_code == 200, v.text
        body = v.json()
    return body
