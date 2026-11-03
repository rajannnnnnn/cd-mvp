"""The stand-in for Meta and the phones, tested on its own: it imports nothing from the product, and the product imports nothing from it."""
from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from metasim import app as appmod

PLATFORM = {"Authorization": "Bearer SIMTOKEN-platform"}
SHOP = {"Authorization": "Bearer SIMTOKEN-business-1"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """A clean simulator for each test, with the product's webhook endpoint replaced by a recorder that answers 200."""
    sim = appmod.sim
    sim.msgs.clear(); sim.wire.clear(); sim.last_customer_msg.clear(); sim.media.clear(); sim.settings.fail_next.clear()
    sim.settings.sent_ms = sim.settings.delivered_ms = sim.settings.read_ms = [1, 2]
    sent: list[dict] = []

    async def send_webhook(payload, label):
        sent.append(payload)
        sim.log_wire("webhook_out", label=label, attempt=1, status=200, ms=7, error=None, body=payload)
        return 200, 7
    monkeypatch.setattr(sim, "send_webhook", send_webhook)
    return sent


def call(method: str, url: str, **kw):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=appmod.app), base_url="http://sim.test") as c:
            return await c.request(method, url, **kw)
    return asyncio.run(go())


def test_the_browser_script_and_the_popup_are_served_for_the_products_sign_up_button():
    js = call("GET", "/sdk.js")
    assert js.status_code == 200 and "login:function" in js.text and "sim_code" in js.text and "window.FB" in js.text
    assert "Connect your WhatsApp number" in call("GET", "/sim/app/signup").text


def test_signing_up_creates_the_shops_number_and_the_code_buys_its_token():
    r = call("POST", "/sim/api/signup/create", json={"name": "Priya Silks", "display": "+91 97000 00055", "coexistence": True}).json()
    assert r["code"] == "sim-code-" + r["phone_number_id"] and r["coexistence"] is True
    tok = call("GET", "/v21.0/oauth/access_token", params={"client_id": "x", "client_secret": "y", "code": r["code"]}).json()["access_token"]
    info = call("GET", f"/v21.0/{r['phone_number_id']}", headers={"Authorization": f"Bearer {tok}"}, params={"fields": "display_phone_number,verified_name,quality_rating"}).json()
    assert info["display_phone_number"] == "+91 97000 00055" and info["verified_name"] == "Priya Silks"
    again = call("POST", "/sim/api/signup/create", json={"name": "Priya Silks", "display": "+919700000055"}).json()
    assert again["phone_number_id"] == r["phone_number_id"]                    # the same number is the same account, not a second one


def test_a_login_code_arrives_on_the_phone_as_the_template_says_and_is_marked_in_the_audit():
    body = {"messaging_product": "whatsapp", "to": "919800001001", "type": "template",
            "template": {"name": "otp_login", "language": {"code": "en"}, "components": [{"type": "body", "parameters": [{"type": "text", "text": "483921"}]}]}}
    assert call("POST", "/v21.0/sim-platform/messages", json=body, headers=PLATFORM).status_code == 200
    phone = call("GET", "/sim/api/phone/919800001001").json()
    plat = next(c for c in phone["chats"] if c["kind"] == "platform")
    assert "483921" in plat["last"]["text"] and "login code" in plat["last"]["text"]
    audit = call("GET", "/sim/api/audit", params={"phone": "919800001001"}).json()
    assert audit["summary"]["otp_codes_delivered"] == 1 and audit["rows"][0]["otp"] is True and audit["rows"][0]["template"] == "otp_login"


def test_a_template_meta_does_not_know_is_refused_like_meta_refuses_it():
    body = {"messaging_product": "whatsapp", "to": "919800001001", "type": "template", "template": {"name": "no_such_template", "language": {"code": "en"}}}
    r = call("POST", "/v21.0/sim-platform/messages", json=body, headers=PLATFORM)
    assert r.status_code == 404 and r.json()["error"]["code"] == 132001


def test_free_text_needs_the_customer_to_have_written_in_the_last_day(fresh):
    text = {"messaging_product": "whatsapp", "to": "919800001002", "type": "text", "text": {"body": "hello"}}
    assert call("POST", "/v21.0/sim-pn-1/messages", json=text, headers=SHOP).json()["error"]["code"] == 131047
    call("POST", "/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800001002", "text": "hi"})
    assert call("POST", "/v21.0/sim-pn-1/messages", json=text, headers=SHOP).status_code == 200
    assert fresh and fresh[0]["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] == "hi"


def test_a_voice_note_can_be_downloaded_by_the_product_with_the_shops_token():
    data = base64.b64encode(b"OggS-fake-audio").decode()
    call("POST", "/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800001003", "kind": "audio", "media_b64": data})
    media_id = next(iter(appmod.sim.media))
    meta = call("GET", f"/v21.0/{media_id}", headers=SHOP).json()
    assert meta["id"] == media_id and meta["url"].endswith(f"/sim/media/{media_id}")
    assert call("GET", f"/sim/media/{media_id}", headers=SHOP).content == b"OggS-fake-audio"
    assert call("GET", f"/sim/media/{media_id}").status_code == 404                     # no token, no file


def test_the_phone_lists_a_chat_with_every_number_newest_first_including_ones_not_yet_used():
    call("POST", "/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800001004", "text": "price of red saree?"})
    chats = call("GET", "/sim/api/phone/919800001004").json()["chats"]
    assert chats[0]["pnid"] == "sim-pn-1" and chats[0]["last"]["text"] == "price of red saree?"
    assert {c["pnid"] for c in chats} >= {"sim-pn-1", "sim-platform"}                  # the platform sender is a contact too, with no messages yet
    assert next(c for c in chats if c["pnid"] == "sim-platform")["messages"] == 0


def test_the_audit_lists_each_message_with_its_ticks_the_products_answer_time_and_exports_csv():
    async def scenario():                                              # one event loop, as in a running server, so the ticks can advance in the background
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=appmod.app), base_url="http://sim.test") as c:
            await c.post("/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800001005", "text": "hi"})
            reply = {"messaging_product": "whatsapp", "to": "919800001005", "type": "text", "text": {"body": "Namaste! How can I help?"}}
            await c.post("/v21.0/sim-pn-1/messages", json=reply, headers=SHOP)
            await asyncio.sleep(0.3)
            return (await c.get("/sim/api/audit", params={"phone": "919800001005"})).json(), await c.get("/sim/api/audit.csv", params={"phone": "919800001005"})
    a, csv = asyncio.run(scenario())
    assert [r["direction"] for r in a["rows"]] == ["phone -> business", "business -> phone (API)"]
    first, second = a["rows"]
    assert first["product_ack_ms"] == 7 and first["webhooks_to_product"][0]["status"] == 200
    assert second["status"] in ("sent", "delivered", "read") and second["sent_at"] is not None
    assert a["summary"]["from_phone"] == 1 and a["summary"]["to_phone"] == 1 and a["summary"]["webhook_failures"] == 0 and a["summary"]["avg_first_reply_s"] is not None
    assert csv.headers["content-type"].startswith("text/csv") and "Namaste! How can I help?" in csv.text and csv.text.splitlines()[0].startswith("time,phone,business")


def test_an_audit_can_be_limited_to_one_business_number():
    call("POST", "/sim/api/customer/send", json={"pnid": "sim-pn-1", "customer": "919800001006", "text": "hello shop"})
    assert call("GET", "/sim/api/audit", params={"phone": "919800001006", "pnid": "sim-platform"}).json()["rows"] == []
    assert len(call("GET", "/sim/api/audit", params={"phone": "919800001006", "pnid": "sim-pn-1"}).json()["rows"]) == 1
