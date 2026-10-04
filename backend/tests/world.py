"""Test world: a real runtime (real Postgres, real queue, real relay + workers) with a simulated
WhatsApp network. Nothing in the product is mocked; the simulator speaks signed Meta webhooks."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from salesai.modules.channels.ingress import accept_webhook
from salesai.modules.channels.simulator import (
    build_account_payload, build_echo_payload, build_message_payload, build_status_payload, signed,
)
from salesai.modules.tenants import add_simulated_number, create_business
from salesai.phone import wa_id
from salesai.queue.postgres import PostgresQueue
from salesai.runtime import Runtime


@dataclass
class Shop:
    business_id: uuid.UUID
    number_id: uuid.UUID
    business_phone: str
    phone_number_id: str
    owner_phone: str
    owner_account_id: uuid.UUID
    business_user_id: uuid.UUID


class World:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.s = rt.settings

    async def make_shop(self, name: str = "Sharma Sarees", *, ai_enabled: bool = True, profile: dict | None = None,
                        owner_phone: str | None = None, business_phone: str | None = None) -> Shop:
        n = uuid.uuid4().int % 10**8
        owner_phone = owner_phone or f"+9198{n:08d}"
        business_phone = business_phone or f"+9197{n:08d}"
        cb = await create_business(self.rt.db, self.s.master_key_bytes, name=name, owner_phone=owner_phone,
                                   owner_name="Owner", ai_enabled=ai_enabled, profile=profile)
        nid = await add_simulated_number(self.rt.db, cb.business_id, business_phone, verified_name=name)
        return Shop(cb.business_id, nid, business_phone, f"sim-{wa_id(business_phone)}", owner_phone,
                    cb.owner_account_id, cb.business_user_id)

    # ---------------- the simulated network -> real ingress
    async def post(self, payload: dict[str, Any], *, signature: str | None = "auto") -> tuple[int, dict]:
        raw, sig = signed(self.s.meta_app_secret, payload)
        return await accept_webhook(self.rt.db, self.s.meta_app_secret, raw, sig if signature == "auto" else signature)

    async def customer_says(self, shop: Shop, phone: str, text: str | None, *, kind: str = "text",
                            wamid: str | None = None, name: str | None = "Priya") -> str:
        payload = build_message_payload(shop.phone_number_id, shop.business_phone, phone, text, kind=kind, wamid=wamid, name=name)
        status, _ = await self.post(payload)
        assert status == 200
        return payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]

    async def owner_replies_from_app(self, shop: Shop, customer_phone: str, text: str) -> None:
        status, _ = await self.post(build_echo_payload(shop.phone_number_id, shop.business_phone, customer_phone, text))
        assert status == 200

    async def status(self, shop: Shop, customer_phone: str, wamid: str, status: str) -> None:
        code, _ = await self.post(build_status_payload(shop.phone_number_id, shop.business_phone, customer_phone, wamid, status))
        assert code == 200

    async def account_event(self, shop: Shop, field: str, event: str) -> None:
        code, _ = await self.post(build_account_payload(shop.phone_number_id, shop.business_phone, field, event))
        assert code == 200

    # ---------------- processing
    async def drain(self, queues: list[str] | None = None, *, rounds: int = 60) -> None:
        """Run relay + workers until nothing more is due."""
        runner = self.rt.worker(queues, tenant_cap=None)
        for _ in range(rounds):
            published = await self.rt.relay.publish_batch()
            ran = await runner.run_once()
            if not published and not ran:
                return
            await asyncio.sleep(0)

    async def drain_for(self, seconds: float, queues: list[str] | None = None) -> None:
        """Keep draining while delayed jobs come due (pacing, end-of-turn waits)."""
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        runner = self.rt.worker(queues, tenant_cap=None)
        while loop.time() < end:
            published = await self.rt.relay.publish_batch()
            ran = await runner.run_once()
            if not published and not ran:
                await asyncio.sleep(0.05)

    # ---------------- inspection (as the tenant, so RLS applies)
    async def q(self, shop: Shop, sql: str, *args: Any) -> list[dict[str, Any]]:
        async with self.rt.db.tenant(shop.business_id) as c:
            return await (await c.execute(sql, args or None)).fetchall()

    async def conv(self, shop: Shop, phone: str) -> dict[str, Any] | None:
        rows = await self.q(shop, "SELECT cv.* FROM conversations cv JOIN customers cu ON cu.id=cv.customer_id WHERE cu.wa_id=%s", wa_id(phone))
        return rows[0] if rows else None

    async def sim_thread(self, phone: str, business_phone: str) -> list[dict[str, Any]]:
        async with self.rt.db.system_tx() as c:
            return await (await c.execute(
                "SELECT * FROM sim_messages WHERE phone=%s AND business_phone=%s ORDER BY id", (phone, business_phone))).fetchall()


def pg_queue_factory(db, s):  # noqa: ANN001, ARG001
    return PostgresQueue(db, backoff_base=0.05)
