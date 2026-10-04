"""The REST API against the real app, database, queue and simulated network."""
from __future__ import annotations

import re
import uuid

import psycopg
import psycopg.rows

from salesai.modules.channels.whatsapp import sign
from tests.test_conversation_flow import PROFILE
from tests.world import configure_fast, settle

V1 = "/api/v1"


async def login(client, phone: str) -> dict:
    r = await client.post(f"{V1}/auth/otp/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    inbox = (await client.get(f"{V1}/sim/inbox", params={"phone": phone})).json()
    code = re.search(r"\b(\d{6})\b", [m for m in inbox if m["template_name"] == "otp_login"][-1]["body"]).group(1)
    r = await client.post(f"{V1}/auth/otp/verify", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def hdr(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def owner(world, client, name="Sharma Sarees", **kw):
    shop = await world.make_shop(name, profile=PROFILE, **kw)
    await configure_fast(world, shop)
    return shop, await login(client, shop.owner_phone)


PRODUCT = {"name": "Banarasi Silk Saree", "description": "Pure silk", "category": "sarees",
           "variants": [{"name": "Red", "availability": "limited", "stock_qty": 3,
                         "policy": {"disclosure": "fixed", "list_price": "1000", "negotiable": True, "ai_may_negotiate": True,
                                    "concession_steps": 3, "floor_price": "812.34", "round_to": "10"}}]}


# ------------------------------------------------------------------ auth + errors
async def test_login_me_refresh_logout_over_http(world, client):
    shop, tok = await owner(world, client)
    assert tok["role"] == "owner" and tok["business_id"] == str(shop.business_id)
    me = (await client.get(f"{V1}/auth/me", headers=hdr(tok))).json()
    assert me["phone"] == shop.owner_phone and me["business"]["name"] == "Sharma Sarees"
    r2 = await client.post(f"{V1}/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r2.status_code == 200
    new = r2.json()
    assert (await client.get(f"{V1}/auth/me", headers=hdr(new))).status_code == 200
    assert (await client.post(f"{V1}/auth/logout", headers=hdr(new))).status_code == 204
    assert (await client.get(f"{V1}/auth/me", headers=hdr(new))).status_code == 401
    assert (await client.post(f"{V1}/auth/refresh", json={"refresh_token": tok["refresh_token"]})).status_code == 401     # rotated + revoked


async def test_error_format_is_consistent(client, world):
    r = await client.get(f"{V1}/products")
    assert r.status_code == 401 and set(r.json()["error"]) == {"code", "message", "details"}
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/products", headers=hdr(tok), json={"name": "x", "variants": []})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error" and r.json()["error"]["details"]
    r = await client.get(f"{V1}/products/{uuid.uuid4()}", headers=hdr(tok))
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_otp_wrong_code_and_validation(client, world):
    shop = await world.make_shop()
    await client.post(f"{V1}/auth/otp/request", json={"phone": shop.owner_phone})
    r = await client.post(f"{V1}/auth/otp/verify", json={"phone": shop.owner_phone, "code": "000000"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_code"
    r = await client.post(f"{V1}/auth/otp/request", json={"phone": "123"})
    assert r.status_code == 422


# ------------------------------------------------------------------ tenancy + floors
async def test_owner_cannot_see_or_touch_another_businesses_data(world, client):
    a_shop, a = await owner(world, client, "A")
    b_shop, b = await owner(world, client, "B")
    await client.post(f"{V1}/products", headers=hdr(a), json=PRODUCT)
    pb = (await client.post(f"{V1}/products", headers=hdr(b), json={**PRODUCT, "name": "B only"})).json()
    assert [p["name"] for p in (await client.get(f"{V1}/products", headers=hdr(a))).json()["items"]] == ["Banarasi Silk Saree"]
    assert (await client.get(f"{V1}/products/{pb['id']}", headers=hdr(a))).status_code == 404
    assert (await client.patch(f"{V1}/products/{pb['id']}", headers=hdr(a), json={"name": "hacked"})).status_code == 404
    assert (await client.delete(f"{V1}/products/{pb['id']}", headers=hdr(a))).status_code == 404
    assert (await client.put(f"{V1}/variants/{pb['variants'][0]['id']}/policy", headers=hdr(a),
                             json={"disclosure": "fixed", "list_price": "1"})).status_code in (404, 422)
    assert (await client.get(f"{V1}/products/{pb['id']}", headers=hdr(b))).json()["name"] == "B only"
    # a tenant id supplied by the client is ignored: there is no such parameter, and a forged header changes nothing
    r = await client.get(f"{V1}/business", headers={**hdr(a), "X-Business-Id": str(b_shop.business_id)})
    assert r.json()["id"] == str(a_shop.business_id)


async def test_floor_price_is_write_only_everywhere(world, client):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=PRODUCT)
    assert r.status_code == 201 and "812.34" not in r.text and "floor_price" not in r.text
    prod = r.json()
    vid = prod["variants"][0]["id"]
    assert prod["variants"][0]["policy"]["floor_set"] is True
    for path in (f"/products/{prod['id']}", "/products", f"/variants/{vid}/policy"):
        resp = await (client.get(f"{V1}{path}", headers=hdr(tok)) if path != f"/variants/{vid}/policy" else client.put(
            f"{V1}{path}", headers=hdr(tok), json={"disclosure": "fixed", "list_price": "1000", "negotiable": True, "ai_may_negotiate": True,
                                                  "concession_steps": 3, "floor_price": "812.34"}))
        assert "812.34" not in resp.text and "floor_price" not in resp.text, path
    # replacing the policy WITHOUT a floor keeps the existing one (never silently clears it)
    r = await client.put(f"{V1}/variants/{vid}/policy", headers=hdr(tok), json={"disclosure": "fixed", "list_price": "1100", "negotiable": True, "ai_may_negotiate": True, "concession_steps": 3})
    assert r.status_code == 200 and r.json()["policy"]["floor_set"] is True
    r = await client.put(f"{V1}/variants/{vid}/policy", headers=hdr(tok), json={"disclosure": "fixed", "list_price": "1100", "clear_floor": True})
    assert r.json()["policy"]["floor_set"] is False
    # business export + audit never carry it
    exp = (await client.post(f"{V1}/business/export", headers=hdr(tok))).text
    assert "812.34" not in exp and "floor_price" not in exp
    audit = await world.q(shop, "SELECT detail FROM audit_log")
    assert "812.34" not in str(audit)


async def test_openapi_contract_exposes_floor_only_on_input_models(client):
    spec = (await client.get("/api/openapi.json")).json()
    schemas = spec["components"]["schemas"]
    write_only_inputs = {"PolicyIn", "LadderIn"}      # request bodies: the owner types a floor, nothing ever echoes it
    for name, sch in schemas.items():
        props = set(sch.get("properties", {}))
        if "floor_price" in props:
            assert name in write_only_inputs, f"{name} must not carry a floor price"
    assert "floor_set" in schemas["PolicyOut"]["properties"]
    assert not {p for p in schemas["LadderOut"]["properties"] if "floor" in p}


async def test_staff_can_work_conversations_but_not_pricing_or_team(world, client):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/team", headers=hdr(tok), json={"phone": "+919876500001", "name": "Raju", "role": "staff"})
    assert r.status_code == 201
    staff = await login(client, "+919876500001")
    assert staff["role"] == "staff"
    prod = (await client.post(f"{V1}/products", headers=hdr(tok), json=PRODUCT)).json()
    assert (await client.get(f"{V1}/products", headers=hdr(staff))).status_code == 200
    assert (await client.put(f"{V1}/variants/{prod['variants'][0]['id']}/policy", headers=hdr(staff), json={"disclosure": "fixed", "list_price": "1"})).status_code == 403
    assert (await client.post(f"{V1}/products", headers=hdr(staff), json=PRODUCT)).status_code == 403
    assert (await client.post(f"{V1}/team", headers=hdr(staff), json={"phone": "+919876500002", "name": "x"})).status_code == 403
    assert (await client.patch(f"{V1}/business", headers=hdr(staff), json={"name": "x"})).status_code == 403
    assert (await client.patch(f"{V1}/variants/{prod['variants'][0]['id']}", headers=hdr(staff), json={"stock_qty": 1})).status_code == 204   # stock updates are staff work
    assert (await client.post(f"{V1}/business/ai", headers=hdr(staff), json={"enabled": False})).status_code == 200


async def test_team_rules(world, client):
    shop, tok = await owner(world, client)
    me = (await client.get(f"{V1}/team", headers=hdr(tok))).json()
    assert len(me) == 1
    assert (await client.delete(f"{V1}/team/{me[0]['id']}", headers=hdr(tok))).status_code == 409       # can't remove yourself / last owner
    assert (await client.post(f"{V1}/team", headers=hdr(tok), json={"phone": "bad", "name": "x"})).status_code == 422


# ------------------------------------------------------------------ catalog
async def test_product_crud_and_idempotent_create(world, client):
    shop, tok = await owner(world, client)
    h = {**hdr(tok), "Idempotency-Key": "abc-123"}
    r1 = await client.post(f"{V1}/products", headers=h, json=PRODUCT)
    r2 = await client.post(f"{V1}/products", headers=h, json=PRODUCT)               # a retried write
    assert r1.status_code == r2.status_code == 201 and r1.json()["id"] == r2.json()["id"]
    assert (await client.get(f"{V1}/products", headers=hdr(tok))).json()["total"] == 1
    r3 = await client.post(f"{V1}/products", headers=h, json={**PRODUCT, "name": "different"})
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "idempotency_key_reuse"
    pid = r1.json()["id"]
    assert (await client.patch(f"{V1}/products/{pid}", headers=hdr(tok), json={"name": "Renamed"})).json()["name"] == "Renamed"
    assert (await client.get(f"{V1}/products", headers=hdr(tok), params={"search": "renamed"})).json()["total"] == 1
    assert (await client.delete(f"{V1}/products/{pid}", headers=hdr(tok))).status_code == 204
    assert (await client.get(f"{V1}/products/{pid}", headers=hdr(tok))).status_code == 404


async def test_invalid_pricing_policies_are_rejected_with_field_errors(world, client):
    shop, tok = await owner(world, client)
    bad = {"name": "X", "variants": [{"policy": {"disclosure": "fixed"}}]}
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=bad)
    assert r.status_code == 422
    bad["variants"][0]["policy"] = {"disclosure": "fixed", "list_price": "100", "floor_price": "150"}
    assert (await client.post(f"{V1}/products", headers=hdr(tok), json=bad)).status_code == 422
    bad["variants"][0]["policy"] = {"disclosure": "fixed", "list_price": "100", "negotiable": True, "ai_may_negotiate": True, "concession_steps": 2}
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=bad)
    assert r.status_code == 422 and "floor" in r.text.lower()           # AI negotiation needs a floor


async def test_offers_and_style_examples(world, client):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/offers", headers=hdr(tok), json={"name": "Diwali 10%", "kind": "percent", "value": "10", "ends_at": "2099-01-01T00:00:00Z"})
    assert r.status_code == 201
    assert (await client.post(f"{V1}/offers", headers=hdr(tok), json={"name": "bad", "kind": "percent", "value": "150"})).status_code == 422
    assert len((await client.get(f"{V1}/offers", headers=hdr(tok))).json()) == 1
    s = await client.post(f"{V1}/style-examples", headers=hdr(tok), json={"situation": "Greeting", "owner_reply": "Namaste ji! Kya dekhna chahenge?"})
    assert s.status_code == 201 and s.json()["situation"] == "greeting"
    assert (await client.delete(f"{V1}/style-examples/{s.json()['id']}", headers=hdr(tok))).status_code == 204


async def test_business_settings_validation_and_audit(world, client):
    shop, tok = await owner(world, client)
    ok = await client.patch(f"{V1}/business", headers=hdr(tok), json={"conversation_settings": {"max_wait_ms": 9000, "business_hours_behavior": "slower"}, "profile": {"address": "New address"}})
    assert ok.status_code == 200 and ok.json()["conversation_settings"]["max_wait_ms"] == 9000 and ok.json()["profile"]["address"] == "New address"
    assert ok.json()["profile"]["hours"] == PROFILE["hours"]                       # patch, not replace
    for bad in ({"conversation_settings": {"max_wait_ms": 10}}, {"conversation_settings": {"nudge_after_minutes": 2000}},
                {"timing_params": {"read_delay": {"mu": 3, "sigma": 0.2, "min_ms": 500, "max_ms": 100}}}, {"timezone": "Mars/Base"}, {"unknown": 1}):
        assert (await client.patch(f"{V1}/business", headers=hdr(tok), json=bad)).status_code == 422, bad
    assert await world.q(shop, "SELECT 1 FROM audit_log WHERE action='conversation_settings.updated'")


# ------------------------------------------------------------------ conversations through the API
async def test_conversation_list_detail_owner_reply_and_window(world, client):
    shop, tok = await owner(world, client)
    await client.post(f"{V1}/products", headers=hdr(tok), json=PRODUCT)
    phone = "+919811100001"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    lst = (await client.get(f"{V1}/conversations", headers=hdr(tok))).json()
    assert len(lst["items"]) == 1
    c = lst["items"][0]
    assert c["customer"]["phone"] == phone and c["lead_stage"] == "interested" and c["window_open"] is True and c["last_message"]["sender"] == "ai"
    det = (await client.get(f"{V1}/conversations/{c['id']}", headers=hdr(tok))).json()
    assert [m["sender"] for m in det["messages"]][:1] == ["customer"] and any("₹1,000" in (m["body"] or "") for m in det["messages"])
    assert "812.34" not in str(det) and "pending_order" not in str(det)
    # the owner jumps in from the dashboard: sent as a person, AI paused
    r = await client.post(f"{V1}/conversations/{c['id']}/messages", headers=hdr(tok), json={"text": "Hi, owner here — I can do ₹950 for you."})
    assert r.status_code == 201
    await world.drain_for(1.0)
    thread = await world.sim_thread(phone, shop.business_phone)
    assert any(m["body"] and "owner here" in m["body"] for m in thread if m["direction"] == "to_user")
    det = (await client.get(f"{V1}/conversations/{c['id']}", headers=hdr(tok))).json()
    assert det["ai_paused_reason"] == "owner_reply" and det["messages"][-1]["sender"] == "owner"
    n_before = len(await world.sim_thread(phone, shop.business_phone))
    await world.customer_says(shop, phone, "ok, send it")
    await world.drain_for(1.2)
    assert len(await world.sim_thread(phone, shop.business_phone)) == n_before + 1      # only the customer's own message; AI stayed quiet
    r = await client.post(f"{V1}/conversations/{c['id']}/resume", headers=hdr(tok))
    assert r.json()["ai_paused_until"] is None
    # outside the 24h window a free-form reply is refused (INV-8), with an actionable error
    async with world.rt.db.tenant(shop.business_id) as cx:
        await cx.execute("UPDATE conversations SET last_inbound_at = now() - interval '26 hours' WHERE id=%s", (c["id"],))
    r = await client.post(f"{V1}/conversations/{c['id']}/messages", headers=hdr(tok), json={"text": "late reply"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "window_closed"


async def test_filters_pagination_and_search(world, client):
    shop, tok = await owner(world, client)
    for i in range(5):
        await world.customer_says(shop, f"+91981110{i:04d}", f"hello {i}", name=f"Cust{i}")
    await world.drain(["inbound.events"])
    page1 = (await client.get(f"{V1}/conversations", headers=hdr(tok), params={"limit": 2})).json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = (await client.get(f"{V1}/conversations", headers=hdr(tok), params={"limit": 2, "cursor": page1["next_cursor"]})).json()
    seen = {c["id"] for c in page1["items"]} | {c["id"] for c in page2["items"]}
    assert len(seen) == 4
    assert (await client.get(f"{V1}/conversations", headers=hdr(tok), params={"search": "Cust3"})).json()["items"][0]["customer"]["name"] == "Cust3"
    assert (await client.get(f"{V1}/conversations", headers=hdr(tok), params={"stage": "won"})).json()["items"] == []
    assert (await client.get(f"{V1}/conversations", headers=hdr(tok), params={"cursor": "garbage"})).status_code == 422


async def test_handoffs_deals_gaps_and_confirmation_rule(world, client):
    shop, tok = await owner(world, client)
    await client.post(f"{V1}/products", headers=hdr(tok), json=PRODUCT)
    phone = "+919811100002"
    await world.customer_says(shop, phone, "do you offer gift wrapping?")
    await settle(world, shop, phone)
    gaps = (await client.get(f"{V1}/knowledge-gaps", headers=hdr(tok))).json()
    assert len(gaps) == 1
    g = gaps[0]["id"]
    assert (await client.post(f"{V1}/knowledge-gaps/{g}/answer", headers=hdr(tok), json={"answer": "Yes, free", "confirm": False})).status_code == 422
    assert (await client.post(f"{V1}/knowledge-gaps/{g}/answer", headers=hdr(tok), json={"answer": "Yes, free gift wrapping", "confirm": True})).status_code == 204
    assert "gift wrapping" in (await client.get(f"{V1}/business", headers=hdr(tok))).text
    hs = (await client.get(f"{V1}/handoffs", headers=hdr(tok))).json()
    assert len(hs) == 1 and hs[0]["reason"] == "unknown_answer" and hs[0]["customer"]["phone"] == phone
    assert (await client.post(f"{V1}/handoffs/{hs[0]['id']}/resolve", headers=hdr(tok))).status_code == 204
    assert (await client.post(f"{V1}/handoffs/{hs[0]['id']}/resolve", headers=hdr(tok))).status_code == 404
    # a visit commitment, then the owner closes it
    await world.customer_says(shop, "+919811100003", "I will come to the shop tomorrow")
    await settle(world, shop, "+919811100003")
    deals = (await client.get(f"{V1}/deals", headers=hdr(tok))).json()
    assert len(deals) == 1 and deals[0]["kind"] == "visit"
    assert (await client.post(f"{V1}/deals/{deals[0]['id']}/close", headers=hdr(tok), json={"status": "won"})).status_code == 204
    assert (await client.get(f"{V1}/deals", headers=hdr(tok), params={"status": "won"})).json()[0]["status"] == "won"


async def test_personal_contacts_and_opt_out_via_api(world, client):
    shop, tok = await owner(world, client)
    r = await client.post(f"{V1}/contacts/personal", headers=hdr(tok), json={"phone": "98111 00004", "name": "Mom"})
    assert r.status_code == 201 and r.json()["is_personal"] is True and r.json()["phone"] == "+919811100004"
    await world.customer_says(shop, "+919811100004", "beta, kya kar rahe ho?")
    await world.drain_for(1.2)
    assert await world.sim_thread("+919811100004", shop.business_phone) == [] or all(m["direction"] == "from_user" for m in await world.sim_thread("+919811100004", shop.business_phone))
    cid = r.json()["id"]
    assert (await client.patch(f"{V1}/customers/{cid}", headers=hdr(tok), json={"is_personal": False, "opted_out": True})).json()["opted_out"] is True
    assert (await client.get(f"{V1}/customers", headers=hdr(tok), params={"opted_out": True})).json()["items"][0]["phone"] == "+919811100004"


async def test_metrics_overview(world, client):
    shop, tok = await owner(world, client)
    await world.customer_says(shop, "+919811100005", "hello")
    await settle(world, shop, "+919811100005")
    m = (await client.get(f"{V1}/metrics/overview", headers=hdr(tok), params={"days": 7})).json()
    assert m["today"]["conversations"] >= 1 and len(m["series"]) == 7 and m["series"][-1]["customer_messages"] >= 1
    assert m["pipeline"].get("exploring", 0) >= 1 and m["ai"]["turns_30d"] >= 1


async def test_pause_and_resume_whole_business(world, client):
    shop, tok = await owner(world, client)
    assert (await client.post(f"{V1}/business/ai", headers=hdr(tok), json={"enabled": False})).json()["ai_enabled"] is False
    await world.customer_says(shop, "+919811100006", "hi")
    await world.drain_for(1.0)
    assert not [m for m in await world.sim_thread("+919811100006", shop.business_phone) if m["direction"] == "to_user"]


# ------------------------------------------------------------------ webhooks over HTTP
async def test_webhook_endpoint_handshake_and_signature(world, client, rt):
    ok = await client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": rt.settings.meta_verify_token, "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert (await client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})).status_code == 403
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    assert (await client.post("/webhooks/whatsapp", content=body)).status_code == 401
    good = await client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(rt.settings.meta_app_secret, body)})
    assert good.status_code == 200


# ------------------------------------------------------------------ simulator guard
async def test_simulator_only_touches_your_own_numbers_and_can_be_disabled(world, client, rt):
    a, ta = await owner(world, client, "SimA")
    b, tb = await owner(world, client, "SimB")
    r = await client.post(f"{V1}/sim/send", headers=hdr(ta), json={"business_phone": b.business_phone, "from_phone": "+919811100007", "text": "hi"})
    assert r.status_code == 404
    r = await client.post(f"{V1}/sim/send", headers=hdr(ta), json={"business_phone": a.business_phone, "from_phone": "+919811100007", "text": "hi"})
    assert r.status_code == 202
    rt.settings.simulator_enabled = False
    try:
        assert (await client.get(f"{V1}/sim/numbers", headers=hdr(ta))).status_code == 404
        assert (await client.get(f"{V1}/sim/inbox", params={"phone": a.owner_phone})).status_code == 404
    finally:
        rt.settings.simulator_enabled = True


# ------------------------------------------------------------------ operator
async def operator_token(world, client):
    async with world.rt.db.system_tx() as c:
        phone = f"+9193{uuid.uuid4().int % 10**8:08d}"
        await c.execute("INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Ops','operator')", (phone,))
    return await login(client, phone)


async def test_operator_console_onboarding_support_and_guards(world, client):
    op = await operator_token(world, client)
    shop, tok = await owner(world, client)
    assert (await client.get(f"{V1}/operator/businesses", headers=hdr(tok))).status_code == 403          # owners are not operators
    assert (await client.get(f"{V1}/operator/businesses")).status_code == 401
    phone = f"+9192{uuid.uuid4().int % 10**8:08d}"
    r = await client.post(f"{V1}/operator/businesses", headers=hdr(op), json={"name": "New Kirana", "owner_name": "Ramesh", "owner_phone": phone, "simulated_number": f"+9190{uuid.uuid4().int % 10**8:08d}"})
    assert r.status_code == 201 and r.json()["numbers"][0]["status"] == "connected"
    owner_login = await login(client, phone)                                                                # the new owner can sign in by number
    assert owner_login["role"] == "owner"
    rows = (await client.get(f"{V1}/operator/businesses", headers=hdr(op))).json()
    assert {x["name"] for x in rows} >= {"New Kirana", "Sharma Sarees"}
    imp = (await client.post(f"{V1}/operator/businesses/{r.json()['id']}/impersonate", headers=hdr(op))).json()
    assert (await client.get(f"{V1}/business", headers=hdr(imp))).json()["name"] == "New Kirana"
    me = (await client.get(f"{V1}/auth/me", headers=hdr(imp))).json()
    assert me["impersonated"] is True
    assert (await client.patch(f"{V1}/operator/businesses/{r.json()['id']}", headers=hdr(op), json={"plan": "growth", "limits": {"llm_tokens_per_day": 1000}})).status_code == 204
    qs = (await client.get(f"{V1}/operator/queues", headers=hdr(op))).json()
    assert {q["queue"] for q in qs} >= {"inbound.events", "conversation.turns", "outbound.actions"}
    assert (await client.get(f"{V1}/operator/costs", headers=hdr(op))).status_code == 200
    assert (await client.get(f"{V1}/operator/turns", headers=hdr(op))).status_code == 200


async def test_operator_sees_alerts_dead_letters_and_can_replay_webhooks(world, client):
    op = await operator_token(world, client)
    shop, tok = await owner(world, client)
    await world.net.account_event(shop.business_phone, "phone_number_quality_update", "FLAGGED")
    await world.drain_for(1.0)
    alerts = (await client.get(f"{V1}/operator/alerts", headers=hdr(op))).json()
    qd = next(a for a in alerts if a["kind"] == "quality_drop" and a["business_id"] == str(shop.business_id))
    assert (await client.post(f"{V1}/operator/alerts/{qd['id']}/resolve", headers=hdr(op))).status_code == 204
    hooks = (await client.get(f"{V1}/operator/webhooks", headers=hdr(op))).json()
    assert hooks
    assert (await client.post(f"{V1}/operator/webhooks/{hooks[0]['id']}/replay", headers=hdr(op))).status_code == 202
    await world.drain_for(0.5)
    assert (await client.post(f"{V1}/operator/queues/inbound.events/dead/999999999/replay", headers=hdr(op))).status_code == 404


async def test_export_and_hard_delete_remove_every_trace(world, client, pg_urls):
    op = await operator_token(world, client)
    shop, tok = await owner(world, client, "Doomed Shop")
    await client.post(f"{V1}/products", headers=hdr(tok), json=PRODUCT)
    phone = "+919811100009"
    await world.customer_says(shop, phone, "silk saree price?")
    await settle(world, shop, phone)
    exp = (await client.get(f"{V1}/operator/businesses/{shop.business_id}/export", headers=hdr(op))).json()
    assert exp["products"] and exp["messages"] and "812.34" not in str(exp)
    r = await client.delete(f"{V1}/operator/businesses/{shop.business_id}", headers=hdr(op), params={"confirm_name": "wrong"})
    assert r.status_code == 422
    r = await client.delete(f"{V1}/operator/businesses/{shop.business_id}", headers=hdr(op), params={"confirm_name": "Doomed Shop"})
    assert r.status_code == 200 and r.json()["deleted"]["businesses"] == 1
    async with await psycopg.AsyncConnection.connect(pg_urls["super"], autocommit=True, row_factory=psycopg.rows.dict_row) as c:   # superuser: sees everything
        for t in ("conversations", "messages", "customers", "products", "turns", "jobs", "outbox"):
            n = (await (await c.execute(f"SELECT count(*) AS n FROM {t} WHERE business_id=%s", (shop.business_id,))).fetchone())["n"]  # noqa: S608
            assert n == 0, t
        assert (await (await c.execute("SELECT count(*) AS n FROM sim_messages WHERE business_phone=%s", (shop.business_phone,))).fetchone())["n"] == 0
        assert (await (await c.execute("SELECT count(*) AS n FROM accounts WHERE id=%s", (shop.owner_account_id,))).fetchone())["n"] == 0
    assert (await client.get(f"{V1}/business", headers=hdr(tok))).status_code == 401           # the owner's sessions died with the tenant
