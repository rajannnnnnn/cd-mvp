"""A shop's data key is made where the master key lives, not where the shop is created (INV-11, ADR 0028).

The web part holds no master key, yet it creates shops at sign-up. The shop's key is only needed to store a WhatsApp token, which only the
gateway does; so creating a shop must work without the master key, and the key is made the first time a token is stored.
Found on the first real start of the web part: sign-up crashed with 'MASTER_KEY is not configured for role api'."""
from __future__ import annotations

import uuid

from salesai.modules.tenants.service import create_business
from salesai.modules.tenants.vault import TokenVault
from tests.test_api import V1, hdr, login

NEW_PHONE = lambda: f"+9194{uuid.uuid4().int % 10**8:08d}"  # noqa: E731


async def _keys(rt, bid) -> int:
    async with rt.db.system_tx() as c:
        return (await (await c.execute("SELECT count(*) AS n FROM business_keys WHERE business_id=%s", (bid,))).fetchone())["n"]


async def test_a_shop_can_be_created_without_the_master_key_and_has_no_data_key_yet(rt):
    cb = await create_business(rt.db, None, name="No Key Yet", owner_phone=NEW_PHONE(), owner_name="Owner")
    assert await _keys(rt, cb.business_id) == 0


async def test_the_first_token_stored_makes_the_key_and_it_opens_the_token_again(rt):
    cb = await create_business(rt.db, None, name="Key On Demand", owner_phone=NEW_PHONE(), owner_name="Owner")
    vault = TokenVault(rt.db, rt.settings.master_key_bytes)
    blob = await vault.encrypt_token(cb.business_id, "EAAG-secret-token")
    assert await _keys(rt, cb.business_id) == 1
    assert await TokenVault(rt.db, rt.settings.master_key_bytes).decrypt_token(cb.business_id, blob) == "EAAG-secret-token"   # a fresh process opens it too
    await vault.encrypt_token(cb.business_id, "second")                                                                         # and it is not made twice
    assert await _keys(rt, cb.business_id) == 1


async def test_a_process_without_the_master_key_still_cannot_open_or_store_a_token(rt):
    cb = await create_business(rt.db, rt.settings.master_key_bytes, name="Has Key", owner_phone=NEW_PHONE(), owner_name="Owner")
    blind = TokenVault(rt.db, None)
    for call in (blind.encrypt_token(cb.business_id, "x"), blind.decrypt_token(cb.business_id, b"y" * 40)):
        try:
            await call
        except RuntimeError as e:
            assert "MASTER_KEY" in str(e)
        else:
            raise AssertionError("a process with no master key opened a token")


async def test_sign_up_works_in_the_web_part_which_holds_no_master_key(world, client, monkeypatch):
    monkeypatch.setattr(world.rt.settings, "master_key", "")                 # the web part's configuration
    tok = await login(client, NEW_PHONE())
    r = await client.post(f"{V1}/onboarding/business", headers=hdr(tok), json={"name": "Web Part Shop", "owner_name": "Owner"})
    assert r.status_code == 201, r.text
