"""Send one signed, Meta-shaped WhatsApp webhook to a running stack and show what the product answers.

    python scripts/send_test_webhook.py "Banarasi silk saree ka price kya hai?"
    python scripts/send_test_webhook.py "hello" --bad-signature       # must be refused with 401
Needs the backend virtualenv and the .env of the running stack (META_APP_SECRET). Defaults match the demo shop on a dev machine."""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from salesai.modules.channels.simulator import build_message_payload, signed

ap = argparse.ArgumentParser()
ap.add_argument("text")
ap.add_argument("--base", default="http://127.0.0.1:8000", help="API/ingress address (the web address also works: /webhooks is proxied)")
ap.add_argument("--shop-phone", default="+919999900002", help="the demo shop's WhatsApp number")
ap.add_argument("--customer", default="+919800000123")
ap.add_argument("--owner", default="+919999900001", help="the shop owner (dev login), used only to read the conversation back")
ap.add_argument("--bad-signature", action="store_true")
a = ap.parse_args()

secret = os.environ["META_APP_SECRET"]
phone_number_id = "sim-" + a.shop_phone.lstrip("+")
payload = build_message_payload(phone_number_id, a.shop_phone, a.customer, a.text, name="Test customer")
raw, sig = signed(secret, payload)
if a.bad_signature:
    sig = "sha256=" + "0" * 64
req = urllib.request.Request(f"{a.base}/webhooks/whatsapp", data=raw, headers={"content-type": "application/json", "x-hub-signature-256": sig}, method="POST")
t0 = time.perf_counter()
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f"webhook answered {r.status} in {(time.perf_counter() - t0) * 1000:.0f} ms: {r.read().decode()}")
except urllib.error.HTTPError as e:
    print(f"webhook answered {e.code} in {(time.perf_counter() - t0) * 1000:.0f} ms: {e.read().decode()}")
    sys.exit(0 if a.bad_signature else 1)
if a.bad_signature:
    sys.exit("expected a refusal, got an acceptance")

def call(path, body=None, token=None):
    h = {"content-type": "application/json"}
    if token:
        h["authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{a.base}/api/v1{path}", data=json.dumps(body).encode() if body is not None else None, headers=h, method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(r, timeout=10))


# sign in as the shop owner (dev: the simulated WhatsApp inbox shows the login code) so the conversation can be read back
import re
import urllib.parse
call("/auth/otp/request", {"phone": a.owner})
time.sleep(1)
code = re.search(r"\b(\d{6})\b", [m for m in call("/sim/inbox?" + urllib.parse.urlencode({"phone": a.owner})) if m["template_name"] == "otp_login"][-1]["body"]).group(1)
token = call("/auth/otp/verify", {"phone": a.owner, "code": code})["access_token"]

print("waiting for the assistant's reply (it is paced like a person, a few seconds)...")
q = urllib.parse.urlencode({"phone": a.customer, "business_phone": a.shop_phone})
for _ in range(40):
    time.sleep(1)
    replies = [m for m in call(f"/sim/thread?{q}", token=token) if m["direction"] == "to_user"]
    if replies:
        time.sleep(4)                                     # a reply may come in several short parts
        for m in [m for m in call(f"/sim/thread?{q}", token=token) if m["direction"] == "to_user"]:
            print(f"  assistant: {m['body']}")
        break
else:
    print("no reply within 40 s (is the assistant switched on for this shop, and a worker running?)")
