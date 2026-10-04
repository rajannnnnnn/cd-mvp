"""Envelope encryption of owner access tokens (INV-11): per-tenant data key wrapped by the master key
(environment / secret store). Tokens are decrypted only inside the outbound sender."""
from __future__ import annotations

import uuid

from salesai import crypto
from salesai.db import Conn, Database


class TokenVault:
    def __init__(self, db: Database, master_key: bytes):
        self.db, self._master = db, master_key
        self._dek_cache: dict[uuid.UUID, bytes] = {}

    async def _dek(self, business_id: uuid.UUID) -> bytes:
        if business_id in self._dek_cache:
            return self._dek_cache[business_id]
        async with self.db.system_tx() as c:
            row = await (await c.execute("SELECT dek_wrapped FROM business_keys WHERE business_id=%s", (business_id,))).fetchone()
        if row is None:
            raise LookupError("no data key for tenant")
        dek = crypto.unwrap_key(self._master, bytes(row["dek_wrapped"]))
        self._dek_cache[business_id] = dek
        return dek

    @staticmethod
    async def create_key(conn: Conn, master_key: bytes, business_id: uuid.UUID) -> None:
        await conn.execute("INSERT INTO business_keys (business_id, dek_wrapped) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                           (business_id, crypto.wrap_key(master_key, crypto.new_data_key())))

    async def encrypt_token(self, business_id: uuid.UUID, token: str) -> bytes:
        return crypto.encrypt(await self._dek(business_id), token, aad=str(business_id).encode())

    async def decrypt_token(self, business_id: uuid.UUID, blob: bytes) -> str:
        return crypto.decrypt(await self._dek(business_id), bytes(blob), aad=str(business_id).encode())
