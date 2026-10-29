"""Send one signed, Meta-shaped WhatsApp webhook to a running stack and show what the product answers.

    python scripts/send_test_webhook.py "Banarasi silk saree ka price kya hai?"
    python scripts/send_test_webhook.py "hello" --bad-signature       # must be refused with 401

Standard library only (no installs). It finds the app secret and the web port by itself in deploy/.env (the Docker stack) or .env
(a development machine), or from META_APP_SECRET / --base. Defaults match the demo shop "Sharma Sarees & Fabrics"."""
import argparse
import hashlib
import hmac
import json
import pathlib
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent


def env_file(path):
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    return out


docker_env, dev_env = env_file(ROOT / "deploy" / ".env"), env_file(ROOT / ".env")
cfg = docker_env or dev_env
ap = argparse.ArgumentParser()
ap.add_argument("text")
ap.add_argument("--base", default=f"http://127.0.0.1:{docker_env.get('WEB_PORT', '8080')}" if docker_env else "http://127.0.0.1:8000",
                help="the web address of the stack (its /webhooks and /api are proxied) or the API on :8000")
ap.add_argument("--shop-phone", default="+919999900002", help="the demo shop's WhatsApp number")
ap.add_argument("--customer", default="+919800000123")
ap.add_argument("--owner", default="+919999900001", help="the shop owner (demo login), used only to read the conversation back")
ap.add_argument("--bad-signature", action="store_true")
a = ap.parse_args()

secret = os.environ.get("META_APP_SECRET") or cfg.get("META_APP_SECRET")
if not secret:
    sys.exit("META_APP_SECRET not found: run from the repository folder after the stack was started, or set the variable.")
wa = lambda p: p.lstrip("+")  # noqa: E731
payload = {"object": "whatsapp_business_account", "entry": [{"id": "sim-waba", "changes": [{"field": "messages", "value": {
    "messaging_product": "whatsapp",
    "metadata": {"display_phone_number": wa(a.shop_phone), "phone_number_id": "sim-" + wa(a.shop_phone)},
    "contacts": [{"profile": {"name": "Test customer"}, "wa_id": wa(a.customer)}],
    "messages": [{"from": wa(a.customer), "id": "wamid.TEST" + uuid.uuid4().hex[:16].upper(), "timestamp": str(int(time.time())),
                  "type": "text", "text": {"body": a.text}}]}}]}]}
raw = json.dumps(payload).encode()
sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
if a.bad_signature:
    sig = "sha256=" + "0" * 64
req = urllib.request.Request(f"{a.base}/webhooks/whatsapp", data=raw, headers={"content-type": "application/json", "x-hub-signature-256": sig}, method="POST")
print(f"sending to {a.base}/webhooks/whatsapp ...")
t0 = time.perf_counter()
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f"webhook answered {r.status} in {(time.perf_counter() - t0) * 1000:.0f} ms: {r.read().decode()}")
except urllib.error.HTTPError as e:
    print(f"webhook answered {e.code} in {(time.perf_counter() - t0) * 1000:.0f} ms: {e.read().decode()}")
    sys.exit(0 if a.bad_signature else 1)
except urllib.error.URLError as e:
    sys.exit(f"could not reach the stack at {a.base}: {e.reason}. Is it running? (use --base to point elsewhere)")
if a.bad_signature:
    sys.exit("expected a refusal, got an acceptance")


def call(path, body=None, token=None):
    h = {"content-type": "application/json"}
    if token:
        h["authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{a.base}/api/v1{path}", data=json.dumps(body).encode() if body is not None else None, headers=h, method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(r, timeout=10))


# sign in as the shop owner (demo: the simulated WhatsApp inbox shows the login code) so the conversation can be read back
call("/auth/otp/request", {"phone": a.owner})
time.sleep(1)
codes = [m for m in call("/sim/inbox?" + urllib.parse.urlencode({"phone": a.owner})) if m["template_name"] == "otp_login"]
token = call("/auth/otp/verify", {"phone": a.owner, "code": re.search(r"\b(\d{6})\b", codes[-1]["body"]).group(1)})["access_token"]

print("waiting for the assistant's reply (it is paced like a person, a few seconds)...")
q = urllib.parse.urlencode({"phone": a.customer, "business_phone": a.shop_phone})
for _ in range(40):
    time.sleep(1)
    if [m for m in call(f"/sim/thread?{q}", token=token) if m["direction"] == "to_user"]:
        time.sleep(4)                                     # a reply may come in several short parts
        for m in [m for m in call(f"/sim/thread?{q}", token=token) if m["direction"] == "to_user"]:
            print(f"  assistant: {m['body']}")
        break
else:
    print("no reply within 40 s (is the assistant switched on for this shop, and is the worker running?)")
