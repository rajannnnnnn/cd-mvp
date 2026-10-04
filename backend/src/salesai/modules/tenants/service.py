"""Tenant onboarding (FR-ON-1..3): business, owner account by phone number, numbers."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from salesai.db import Database, jsonb
from salesai.modules.tenants import defaults
from salesai.modules.tenants.vault import TokenVault
from salesai.phone import normalize_phone, wa_id


@dataclass
class CreatedBusiness:
    business_id: uuid.UUID
    owner_account_id: uuid.UUID
    business_user_id: uuid.UUID


async def upsert_account(conn, phone: str, name: str | None = None, language: str = "en") -> uuid.UUID:  # noqa: ANN001
    row = await (await conn.execute(
        """INSERT INTO accounts (phone, name, language) VALUES (%s,%s,%s)
           ON CONFLICT (phone) DO UPDATE SET name = COALESCE(accounts.name, EXCLUDED.name)
           RETURNING id""", (phone, name, language))).fetchone()
    return row["id"]


async def create_business(db: Database, master_key: bytes, *, name: str, owner_phone: str, owner_name: str,
                          timezone: str = "Asia/Kolkata", plan: str = "pilot", language: str = "en",
                          profile: dict | None = None, ai_enabled: bool = False) -> CreatedBusiness:
    phone = normalize_phone(owner_phone)
    bid = uuid.uuid4()
    async with db.system_tx() as s:
        await s.execute("INSERT INTO businesses (id, name, timezone, plan) VALUES (%s,%s,%s,%s)", (bid, name, timezone, plan))
        await TokenVault.create_key(s, master_key, bid)
        account_id = await upsert_account(s, phone, owner_name, language)
    async with db.tenant(bid) as c:
        await c.execute(
            """UPDATE businesses SET profile=%s, sales_settings=%s, conversation_settings=%s, timing_params=%s,
                      limits=%s, ai_enabled=%s WHERE id=%s""",
            (jsonb(profile or {}), jsonb(defaults.SALES_SETTINGS), jsonb(defaults.CONVERSATION_SETTINGS),
             jsonb(defaults.TIMING_PARAMS), jsonb(defaults.LIMITS), ai_enabled, bid))
        bu = await (await c.execute(
            "INSERT INTO business_users (business_id, account_id, name, role) VALUES (%s,%s,%s,'owner') RETURNING id",
            (bid, account_id, owner_name))).fetchone()
        await c.execute(
            "INSERT INTO audit_log (business_id, actor_label, action, entity, entity_id) VALUES (%s,'operator','business.created','business',%s)",
            (bid, str(bid)))
    return CreatedBusiness(bid, account_id, bu["id"])


async def add_simulated_number(db: Database, business_id: uuid.UUID, display_phone: str, verified_name: str | None = None) -> uuid.UUID:
    phone = normalize_phone(display_phone)
    async with db.tenant(business_id) as c:
        row = await (await c.execute(
            """INSERT INTO whatsapp_numbers (business_id, channel, phone_number_id, waba_id, display_phone, verified_name, status)
               VALUES (%s,'simulator',%s,'sim-waba',%s,%s,'connected') RETURNING id""",
            (business_id, f"sim-{wa_id(phone)}", phone, verified_name))).fetchone()
    return row["id"]


async def add_cloud_number(db: Database, vault: TokenVault, business_id: uuid.UUID, *, phone_number_id: str,
                           waba_id: str, display_phone: str, access_token: str, coexistence: bool,
                           verified_name: str | None = None) -> uuid.UUID:
    token_enc = await vault.encrypt_token(business_id, access_token)
    async with db.tenant(business_id) as c:
        row = await (await c.execute(
            """INSERT INTO whatsapp_numbers (business_id, channel, phone_number_id, waba_id, display_phone, verified_name,
                                             access_token_enc, coexistence)
               VALUES (%s,'whatsapp_cloud',%s,%s,%s,%s,%s,%s) RETURNING id""",
            (business_id, phone_number_id, waba_id, normalize_phone(display_phone), verified_name, token_enc, coexistence))).fetchone()
    return row["id"]
