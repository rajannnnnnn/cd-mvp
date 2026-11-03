"""Optional contract check: the product's own Meta code (sending, sign-up, webhook parsing and signature check, media download) against this
simulator, with nothing in between. It needs the product installed (the backend's virtualenv); without it these tests skip. The product's
deployed parts never contain this file or the simulator; it lives here, outside, and only reads the product's public pieces."""
from __future__ import annotations

import asyncio
import base64
import json
import uuid

import httpx
import pytest

pytest.importorskip("salesai")
from fastapi import FastAPI, Request
from salesai.modules.channels.base import (
    AccountEvent,
    InboundMessage,
    NumberRef,
    StatusUpdate,
)
from salesai.modules.channels.embedded_signup import complete_signup
from salesai.modules.channels.whatsapp import (
    CloudApiChannel,
    parse_webhook,
    verify_signature,
)

from metasim import app as appmod

SECRET = "contract-secret-123456"


def sim_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=appmod.app), base_url="http://sim.test")


def number(pnid: str, token: str, display: str) -> NumberRef:
    return NumberRef(id=uuid.uuid4(), business_id=uuid.uuid4(), channel="whatsapp_cloud", phone_number_id=pnid, display_phone=display, access_token=token)


SEEN: list[tuple[bytes, str]] = []
PRODUCT = FastAPI()


@PRODUCT.post("/webhooks/whatsapp")
async def hook(request: Request):
    SEEN.append((await request.body(), request.headers.get("x-hub-signature-256", "")))
    return {"ok": True}


@pytest.fixture(autouse=True)
def clean():
    """A clean simulator whose webhooks go to a recording endpoint that answers 200 at once (a dead address would make it retry, like Meta)."""
    s = appmod.sim
    s.msgs.clear(); s.wire.clear(); s.last_customer_msg.clear(); s.media.clear(); SEEN.clear()
    s.settings.app_secret = SECRET
    s.settings.sent_ms = s.settings.delivered_ms = s.settings.read_ms = [1, 2]
    old, s.client, s.settings.webhook_url = s.client, httpx.AsyncClient(transport=httpx.ASGITransport(app=PRODUCT)), "http://product.test/webhooks/whatsapp"
    yield
    s.client = old


def test_the_products_channel_sends_text_templates_and_reads_against_the_simulator():
    async def go():
        async with sim_client() as http:
            http.base_url = httpx.URL("http://sim.test")
            ch = CloudApiChannel("http://sim.test", "v21.0", client=http)
            shop = number("sim-pn-1", "SIMTOKEN-business-1", "+919700000001")
            platform = number("sim-platform", "SIMTOKEN-platform", "+919700000000")
            blocked = await ch.send_text(shop, "919800002001", "hello")                         # no customer message yet: Meta's 24-hour rule
            await http.post("/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800002001", "text": "hi"})
            ok = await ch.send_text(shop, "919800002001", "Namaste!")
            otp = await ch.send_template(platform, "919800002001", "otp_login", "en", ["777123"])
            read = await ch.mark_read(shop, appmod.sim.msgs[0].id)
            return blocked, ok, otp, read, (await http.get("/sim/api/phone/919800002001")).json()
    blocked, ok, otp, read, phone = asyncio.run(go())
    assert blocked.ok is False and blocked.permanent == "window_closed"
    assert ok.ok and ok.message_id and otp.ok and read.ok
    assert any("777123" in (c["last"] or {}).get("text", "") for c in phone["chats"])                      # the code is on the phone


def test_the_products_sign_up_finish_works_against_the_simulators_popup_and_code():
    async def go():
        async with sim_client() as http:
            made = (await http.post("/sim/api/signup/create", json={"name": "Contract Shop", "display": "+91 97000 00077", "coexistence": False})).json()
            return made, await complete_signup(graph_base="http://sim.test", version="v21.0", app_id="sim-app", app_secret="x", code=made["code"],
                                               waba_id=made["waba_id"], phone_number_id=made["phone_number_id"], coexistence=False, client=http)
    made, res = asyncio.run(go())
    assert res.display_phone == "+91 97000 00077" and res.verified_name == "Contract Shop" and res.access_token == appmod.sim.biz[made["phone_number_id"]].token


def test_a_webhook_from_the_simulator_passes_the_products_signature_check_and_parses():
    async def go():
        async with sim_client() as http:
            await http.post("/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800002002", "name": "Asha", "text": "price?"})
            await http.post("/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800002002", "kind": "audio", "media_b64": base64.b64encode(b"voice").decode()})
            await http.post("/sim/api/account-event", json={"pnid": "sim-pn-1", "event": "PARTNER_REMOVED"})
    asyncio.run(go())
    seen = SEEN
    assert len(seen) >= 3
    for raw, sig in seen:
        assert verify_signature(SECRET, raw, sig)                                                     # signed the way Meta signs
    events = [e for raw, _ in seen for e in parse_webhook(json.loads(raw))]
    text = next(e for e in events if isinstance(e, InboundMessage) and e.kind == "text")
    assert text.text == "price?" and text.sender_name == "Asha" and text.from_id == "919800002002"
    voice = next(e for e in events if isinstance(e, InboundMessage) and e.kind == "audio")
    assert voice.media_id and voice.media_id.startswith("sim-media-")
    assert any(isinstance(e, AccountEvent) and e.kind == "disconnected" for e in events)


def test_the_products_media_download_and_status_events_work_against_the_simulator():
    async def go():
        async with sim_client() as http:
            ch = CloudApiChannel("http://sim.test", "v21.0", client=http)
            shop = number("sim-pn-1", "SIMTOKEN-business-1", "+919700000001")
            await http.post("/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800002003", "kind": "audio", "media_b64": base64.b64encode(b"hello-voice").decode()})
            media_id = next(iter(appmod.sim.media))
            data = await ch.download_media(shop, media_id)
            sent = await ch.send_text(shop, "919800002003", "ok")
            await asyncio.sleep(0.3)
            return data, sent
    data, sent = asyncio.run(go())
    assert data == b"hello-voice" and sent.ok
    status_hooks = [e for e in appmod.sim.wire if e.get("dir") == "webhook_out" and str(e.get("label", "")).startswith("status:")]
    parsed = [x for e in status_hooks for x in parse_webhook(e["body"]) if isinstance(x, StatusUpdate)]
    assert {x.status for x in parsed} >= {"sent", "delivered"}
