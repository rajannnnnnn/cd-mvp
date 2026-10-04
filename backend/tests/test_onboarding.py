"""Self-serve sign-up and onboarding: new number -> setup session -> business -> products -> number -> live, with slug URLs."""
from __future__ import annotations

import uuid

from tests.test_api import PRODUCT, V1, hdr, login

NEW_PHONE = lambda: f"+9193{uuid.uuid4().int % 10**8:08d}"  # noqa: E731


async def signup(world, client, phone=None):
    phone = phone or NEW_PHONE()
    return phone, await login(client, phone)


async def test_new_number_creates_a_business_and_gets_owner_tokens(world, client):
    phone, tok = await signup(world, client)
    assert tok["role"] == "setup" and tok["business_id"] is None
    me = (await client.get(f"{V1}/auth/me", headers=hdr(tok))).json()
    assert me["role"] == "setup" and me["business"] is None
    st = (await client.get(f"{V1}/onboarding", headers=hdr(tok))).json()
    assert st["has_business"] is False and st["steps"][0]["key"] == "business"
    # a setup session can do nothing else
    assert (await client.get(f"{V1}/products", headers=hdr(tok))).status_code == 403
    assert (await client.get(f"{V1}/business", headers=hdr(tok))).status_code == 403

    r = await client.post(f"{V1}/onboarding/business", headers=hdr(tok),
                          json={"name": "Meera's Saree Studio!", "owner_name": "Meera", "category": "Clothing & fabrics", "city": "Pune"})
    assert r.status_code == 201, r.text
    owner = r.json()
    assert owner["role"] == "owner" and owner["business_id"]
    me = (await client.get(f"{V1}/auth/me", headers=hdr(owner))).json()
    assert me["business"]["slug"] == "meera-s-saree-studio" and me["name"] == "Meera"
    assert (await client.get(f"{V1}/auth/me", headers=hdr(tok))).status_code == 401           # the setup session was ended
    b = (await client.get(f"{V1}/business", headers=hdr(owner))).json()
    assert b["status"] == "onboarding" and b["ai_enabled"] is False and b["plan"] == "trial"
    assert b["profile"]["city"] == "Pune" and b["sales_settings"]["language_default"] == "hinglish"
    # a setup session cannot create a second business, an owner cannot use the setup call
    assert (await client.post(f"{V1}/onboarding/business", headers=hdr(owner), json={"name": "Another", "owner_name": "M"})).status_code == 403


async def test_progress_steps_and_go_live(world, client):
    _, tok = await signup(world, client)
    owner = (await client.post(f"{V1}/onboarding/business", headers=hdr(tok), json={"name": "Quick Mart", "owner_name": "Ravi"})).json()
    st = (await client.get(f"{V1}/onboarding", headers=hdr(owner))).json()
    keys = {s["key"]: s for s in st["steps"]}
    assert keys["business"]["done"] and not keys["products"]["done"] and not st["ready_to_go_live"] and not st["complete"]
    # cannot go live without a product
    assert (await client.post(f"{V1}/onboarding/go-live", headers=hdr(owner))).status_code == 409

    assert (await client.patch(f"{V1}/business", headers=hdr(owner), json={"profile": {"address": "12 MG Road, Pune", "hours": "10am-8pm"}})).status_code == 200
    assert (await client.post(f"{V1}/products", headers=hdr(owner), json=PRODUCT)).status_code in (200, 201)
    st = (await client.get(f"{V1}/onboarding", headers=hdr(owner))).json()
    keys = {s["key"]: s for s in st["steps"]}
    assert keys["details"]["done"] and keys["products"]["done"] and st["ready_to_go_live"]

    r = (await client.post(f"{V1}/onboarding/steps", headers=hdr(owner), json={"step": "whatsapp", "action": "skip"})).json()
    assert {s["key"]: s for s in r["steps"]}["whatsapp"]["skipped"] is True
    r = await client.post(f"{V1}/onboarding/go-live", headers=hdr(owner))
    assert r.status_code == 200 and r.json()["complete"] is True
    b = (await client.get(f"{V1}/business", headers=hdr(owner))).json()
    assert b["status"] == "active" and b["ai_enabled"] is False                       # nothing to answer on yet

    # connecting a number later switches the assistant on by itself
    n = await client.post(f"{V1}/numbers/test", headers=hdr(owner))
    assert n.status_code == 201, n.text
    assert (await client.get(f"{V1}/business", headers=hdr(owner))).json()["ai_enabled"] is True


async def test_slugs_are_unique_reserved_and_changeable(world, client):
    _, t1 = await signup(world, client)
    a = (await client.post(f"{V1}/onboarding/business", headers=hdr(t1), json={"name": "Twin Shop", "owner_name": "A"})).json()
    _, t2 = await signup(world, client)
    b = (await client.post(f"{V1}/onboarding/business", headers=hdr(t2), json={"name": "Twin Shop", "owner_name": "B"})).json()
    sa = (await client.get(f"{V1}/auth/me", headers=hdr(a))).json()["business"]["slug"]
    sb = (await client.get(f"{V1}/auth/me", headers=hdr(b))).json()["business"]["slug"]
    assert sa != sb and sb.startswith("twin-shop")

    _, t3 = await signup(world, client)
    c = (await client.post(f"{V1}/onboarding/business", headers=hdr(t3), json={"name": "Pricing", "owner_name": "C"})).json()
    assert (await client.get(f"{V1}/auth/me", headers=hdr(c))).json()["business"]["slug"] != "pricing"          # reserved word

    chk = (await client.get(f"{V1}/onboarding/slug", headers=hdr(c), params={"slug": sa})).json()
    assert chk["available"] is False and chk["suggestion"] != sa
    assert (await client.get(f"{V1}/onboarding/slug", headers=hdr(c), params={"slug": "login"})).json()["available"] is False
    assert (await client.get(f"{V1}/onboarding/slug", headers=hdr(c), params={"slug": f"free-{uuid.uuid4().hex[:8]}"})).json()["available"] is True

    r = await client.patch(f"{V1}/business", headers=hdr(c), json={"slug": sa})
    assert r.status_code == 409
    assert (await client.patch(f"{V1}/business", headers=hdr(c), json={"slug": "Bad Slug!"})).status_code == 422
    new = f"my-{uuid.uuid4().hex[:8]}"
    r = await client.patch(f"{V1}/business", headers=hdr(c), json={"slug": new})
    assert r.status_code == 200 and r.json()["slug"] == new


async def test_returning_owner_signs_straight_into_their_shop(world, client):
    phone, t = await signup(world, client)
    await client.post(f"{V1}/onboarding/business", headers=hdr(t), json={"name": "Return Shop", "owner_name": "R"})
    again = await login(client, phone)
    assert again["role"] == "owner" and again["business_id"]
