"""Resolves a business number to its channel adapter and a NumberRef with a just-in-time decrypted token."""
from __future__ import annotations

import uuid

from salesai.db import Database
from salesai.modules.channels.base import MessagingChannel, NumberRef
from salesai.modules.channels.simulator import SimulatorChannel
from salesai.modules.channels.whatsapp import CloudApiChannel
from salesai.modules.tenants import TokenVault


class ChannelRegistry:
    def __init__(self, db: Database, vault: TokenVault, *, graph_base: str, graph_version: str,
                 simulator_enabled: bool, cloud: CloudApiChannel | None = None):
        self.db, self.vault = db, vault
        self.simulator = SimulatorChannel(db) if simulator_enabled else None
        self.cloud = cloud or CloudApiChannel(graph_base, graph_version)

    def channel(self, name: str) -> MessagingChannel:
        if name == "simulator":
            if self.simulator is None:
                raise RuntimeError("simulator channel is disabled in this environment")
            return self.simulator
        return self.cloud

    async def number(self, business_id: uuid.UUID, number_id: uuid.UUID) -> tuple[NumberRef, MessagingChannel]:
        async with self.db.tenant(business_id) as c:
            r = await (await c.execute("SELECT * FROM whatsapp_numbers WHERE id=%s", (number_id,))).fetchone()
        if r is None:
            raise LookupError("number not found")
        token = await self.vault.decrypt_token(business_id, r["access_token_enc"]) if r["access_token_enc"] else None
        ref = NumberRef(r["id"], business_id, r["channel"], r["phone_number_id"], r["display_phone"], token)
        return ref, self.channel(r["channel"])
