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
        from salesai.modules.channels.simulator import SimulatorNetwork
        self.net = SimulatorNetwork(rt.db, rt.settings.meta_app_secret)

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
        if wamid is not None:     # explicit redelivery of a known message id (idempotency tests)
            payload = build_message_payload(shop.phone_number_id, shop.business_phone, phone, text, kind=kind, wamid=wamid, name=name)
            status, _ = await self.post(payload)
            assert status == 200
            return wamid
        return await self.net.user_sends(shop.business_phone, phone, text, kind=kind, name=name)

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
                "SELECT * FROM sim_messages WHERE phone=%s AND business_phone=%s ORDER BY id", (wa_id(phone), business_phone))).fetchall()


def pg_queue_factory(db, s):  # noqa: ANN001, ARG001
    return PostgresQueue(db, backoff_base=0.05)


# ------------------------------------------------------------------ conversation-flow helpers
import json as _json  # noqa: E402
from decimal import Decimal  # noqa: E402

from salesai.db import jsonb  # noqa: E402
from salesai.modules.agent.llm.base import LLMRequest, LLMResult  # noqa: E402
from salesai.modules.agent.llm.chain import ResilientLLM  # noqa: E402
from salesai.modules.agent.llm.local import LocalRulesProvider  # noqa: E402
from salesai.modules.catalog import PolicyIn, ProductIn, VariantIn, repo  # noqa: E402

FAST_CONV = {"first_check_ms": 20, "max_wait_ms": 1500, "min_quiet_complete_ms": 60, "min_quiet_incomplete_ms": 250,
             "default_gap_ms": 100, "owner_pause_minutes": 120, "business_hours_behavior": "reply_normally",
             "nudge_after_minutes": 240, "history_messages": 20}
FAST_TIMING = {"read_delay": {"mu": 3.0, "sigma": 0.2, "min_ms": 10, "max_ms": 40},
               "typing_ms_per_char": {"mu": 0.5, "sigma": 0.1, "min_ms": 0, "max_ms": 2},
               "part_gap": {"mu": 3.0, "sigma": 0.2, "min_ms": 10, "max_ms": 40}, "max_total_delay_ms": 900, "slower_multiplier": 2.5}


async def configure_fast(world: World, shop: Shop, *, conv: dict | None = None, timing: dict | None = None) -> None:
    async with world.rt.db.tenant(shop.business_id) as c:
        await c.execute("UPDATE businesses SET conversation_settings=%s, timing_params=%s WHERE id=%s",
                        (jsonb({**FAST_CONV, **(conv or {})}), jsonb({**FAST_TIMING, **(timing or {})}), shop.business_id))


async def add_product(world: World, shop: Shop, name: str, price: str | None, *, floor: str | None = None, steps: int = 3,
                      negotiable: bool = True, disclosure: str = "fixed", variant: str = "default", description: str | None = None,
                      category: str | None = None, availability: str = "in_stock", stock: int | None = None, rng: tuple[str, str] | None = None,
                      requires: list | None = None, round_to: str = "10", aliases: list[str] | None = None) -> Any:
    pol = PolicyIn(disclosure=disclosure, list_price=Decimal(price) if price else None,
                   range_min=Decimal(rng[0]) if rng else None, range_max=Decimal(rng[1]) if rng else None,
                   negotiable=negotiable and floor is not None, ai_may_negotiate=floor is not None and negotiable,
                   concession_steps=steps if floor is not None else 0, floor_price=Decimal(floor) if floor else None,
                   concession_requires=requires or [], round_to=Decimal(round_to))
    async with world.rt.db.tenant(shop.business_id) as c:
        pid = await repo.create_product(c, shop.business_id, ProductIn(
            name=name, description=description, category=category, attributes={"aliases": aliases or []},
            variants=[VariantIn(name=variant, availability=availability, stock_qty=stock, policy=pol)]))
        return (await repo.get_product(c, pid)).variants[0].id


class Recorder:
    """Wraps a provider and records every request it receives (for INV-1 inspection)."""
    name = "recorder"

    def __init__(self, inner, script=None):
        self.inner, self.requests, self.script = inner, [], script

    async def generate(self, req: LLMRequest, schema):
        self.requests.append(req)
        if self.script is not None:
            out = self.script(req)
            if out is not None:
                return LLMResult(output=schema.model_validate(out), provider="scripted", model="scripted", input_tokens=0, output_tokens=0, latency_ms=0)
        return await self.inner.generate(req, schema)


def install_llm(world: World, provider) -> Any:
    old = world.rt.agent.llm
    world.rt.agent.llm = ResilientLLM(provider)
    return old


async def sim_texts(world: World, shop: Shop, phone: str) -> list[str]:
    rows = await world.sim_thread(phone, shop.business_phone)
    return [r["body"] for r in rows if r["direction"] == "to_user" and r["kind"] in ("text",)]


async def settle(world: World, shop: Shop, phone: str, *, want: int = 1, timeout: float = 8.0) -> list[str]:
    """Drain until at least `want` outbound texts exist (or timeout)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    texts: list[str] = []
    while loop.time() < end:
        await world.drain_for(0.25)
        texts = await sim_texts(world, shop, phone)
        if len(texts) >= want:
            await world.drain_for(0.3)         # let any remaining parts through
            return await sim_texts(world, shop, phone)
    return texts


def llm_json(rec: Recorder) -> str:
    return _json.dumps([r.input for r in rec.requests], default=str)
