"""Signing in to the admin plane the way the founder does: a WhatsApp code, then an authenticator code."""
from __future__ import annotations

import re
import uuid

from salesai.modules.auth import totp
from salesai.modules.auth.admin import enroll_admin

A1 = "/admin/v1"


async def make_admin(world, phone: str | None = None) -> tuple[str, str]:
    """A platform operator account with an authenticator enrolled. Returns (phone, authenticator secret)."""
    phone = phone or f"+9193{uuid.uuid4().int % 10**8:08d}"
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Founder','operator') ON CONFLICT (phone) DO UPDATE SET platform_role='operator'", (phone,))
    return phone, await enroll_admin(world.rt.db, world.rt.settings, phone)


def code_for(rt, secret: str, *, steps_ahead: int = 0) -> str:
    return totp.code_at(secret, int(rt.admin_auth.clock() // 30) + steps_ahead)


async def whatsapp_code(client, phone: str) -> str:
    inbox = (await client.get("/api/v1/sim/inbox", params={"phone": phone})).json()
    return re.search(r"\b(\d{6})\b", [m for m in inbox if m["template_name"] == "otp_login"][-1]["body"]).group(1)


async def ticket(client, phone: str) -> str:
    r = await client.post(f"{A1}/auth/code/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    r = await client.post(f"{A1}/auth/code/verify", json={"phone": phone, "code": await whatsapp_code(client, phone)})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


async def admin_login(client, rt, phone: str, secret: str, *, steps_ahead: int = 0) -> dict:
    t = await ticket(client, phone)
    r = await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": code_for(rt, secret, steps_ahead=steps_ahead)})
    assert r.status_code == 200, r.text
    return r.json()
