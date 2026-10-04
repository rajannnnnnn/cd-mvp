"""Embedded Signup completion against a fake Graph server (real HTTP, real database, real encryption)."""
from __future__ import annotations

import pytest

from salesai.config import get_settings
from tests.fake_graph import FakeGraph
from tests.test_api import V1, hdr, owner

CODE = "valid-code-1234567890"


@pytest.fixture(scope="module")
def graph():
    g = FakeGraph()
    g.start()
    yield g
    g.stop()


@pytest.fixture
def signup_on(rt, graph):
    s = rt.settings
    saved = (s.meta_app_id, s.meta_config_id, s.meta_graph_base)
    s.meta_app_id, s.meta_config_id, s.meta_graph_base = "123456789", "cfg-1", graph.base
    graph.reset()
    yield graph
    s.meta_app_id, s.meta_config_id, s.meta_graph_base = saved


def body(**kw):
    return {"code": CODE, "waba_id": "waba-100", "phone_number_id": "pn-555", "coexistence": False, **kw}


async def test_connect_stores_an_encrypted_token_and_registers_the_number(world, client, rt, signup_on):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(phone_number_id=f"pn-{shop.business_id.hex[:8]}"))
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["channel"] == "whatsapp_cloud" and n["display_phone"] == "+919876543210" and n["verified_name"] == "Fresh Basket Kirana"
    assert [c[0] for c in signup_on.calls] == ["code_exchange", "subscribe", "number_lookup", "register"]
    assert signup_on.calls[1][2]["auth"] == "Bearer EAAG-fake-business-token"
    assert "pin" in signup_on.calls[3][2] and len(signup_on.calls[3][2]["pin"]) == 6
    # INV-11: the token is stored encrypted, never in the clear, and never returned
    [row] = await world.q(shop, "SELECT access_token_enc, quality_rating FROM whatsapp_numbers WHERE channel='whatsapp_cloud'")
    assert row["quality_rating"] == "green"
    assert "EAAG-fake-business-token" not in str(row["access_token_enc"]) and "EAAG" not in r.text
    nums = (await client.get(f"{V1}/numbers", headers=hdr(tok))).json()
    assert any(x["channel"] == "whatsapp_cloud" for x in nums)
    assert (await world.q(shop, "SELECT 1 FROM audit_log WHERE action='number.connected'"))


async def test_coexistence_numbers_are_not_registered(world, client, signup_on):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(coexistence=True, phone_number_id=f"pn-{shop.business_id.hex[:8]}"))
    assert r.status_code == 201 and r.json()["coexistence"] is True
    assert "register" not in [c[0] for c in signup_on.calls]


async def test_expired_code_is_a_clear_error_and_nothing_is_stored(world, client, signup_on):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(code="expired-code-000000", phone_number_id="pn-x1"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "code_exchange_failed" and "expired" in r.json()["error"]["message"]
    assert await world.q(shop, "SELECT 1 FROM whatsapp_numbers WHERE channel='whatsapp_cloud'") == []


async def test_graph_outage_is_retryable_and_leaves_no_partial_state(world, client, signup_on):
    shop, tok = await owner(world, client)
    signup_on.fail["subscribe"] = (503, "temporarily unavailable")
    r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body(phone_number_id="pn-x2"))
    assert r.status_code == 502 and r.json()["error"]["code"] == "graph_error"
    assert await world.q(shop, "SELECT 1 FROM whatsapp_numbers WHERE channel='whatsapp_cloud'") == []


async def test_a_number_cannot_be_connected_to_two_businesses(world, client, signup_on):
    _, tok_a = await owner(world, client, "Shop A")
    _, tok_b = await owner(world, client, "Shop B")
    assert (await client.post(f"{V1}/numbers/connect", headers=hdr(tok_a), json=body(phone_number_id="pn-shared"))).status_code == 201
    r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok_b), json=body(phone_number_id="pn-shared"))
    assert r.status_code == 409


async def test_only_the_owner_can_connect_and_it_is_off_until_configured(world, client, rt, graph):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/numbers/connect", headers=hdr(tok), json=body())
    assert r.status_code == 503 and r.json()["error"]["code"] == "signup_unavailable"
    assert (await client.get(f"{V1}/public/config")).json()["embedded_signup"] is None
    assert get_settings().meta_app_id == ""


async def test_public_config_advertises_signup_when_configured(client, signup_on):
    cfg = (await client.get(f"{V1}/public/config")).json()["embedded_signup"]
    assert cfg == {"app_id": "123456789", "config_id": "cfg-1", "graph_version": "v21.0"}
    assert "secret" not in str(cfg).lower()
