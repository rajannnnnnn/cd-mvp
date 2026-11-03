"""Optional end-to-end drive: a shop owner signs up, connects a number and a customer chats with it, through the product's real HTTP API and
the simulator's phones. Needs a running product (role `all` or all parts) whose Meta settings point at the simulator, and the simulator running.
It uses only HTTP, so it works against the five-part deployment too.

  python drive_local_product.py --product http://127.0.0.1:8000 --sim http://127.0.0.1:9100
"""
from __future__ import annotations

import argparse
import re
import sys
import time

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default="http://127.0.0.1:8000")
    ap.add_argument("--sim", default="http://127.0.0.1:9100")
    ap.add_argument("--owner", default="+919810001001", help="the owner's phone number (any number: the simulator is its phone)")
    ap.add_argument("--customer", default="+919810002002")
    a = ap.parse_args()
    api, sim = a.product.rstrip("/") + "/api/v1", a.sim.rstrip("/")
    owner_wa = re.sub(r"\D", "", a.owner)
    cust_wa = re.sub(r"\D", "", a.customer)
    c = httpx.Client(timeout=30)

    def step(msg: str) -> None:
        print(f"  - {msg}", flush=True)

    def wait(fn, what: str, secs: float = 40.0):
        end = time.time() + secs
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.5)
        raise SystemExit(f"timed out waiting for: {what}")

    print("1. The owner signs in: the product sends the code through Meta's API; it arrives on the owner's phone in the simulator")
    r = c.post(f"{api}/auth/otp/request", json={"phone": a.owner})
    step(f"asked for a sign-in code: {r.status_code}")

    def latest_code() -> str | None:
        chats = c.get(f"{sim}/sim/api/phone/{owner_wa}").json()["chats"]
        plat = next((x for x in chats if x["kind"] == "platform" and x["last"]), None)
        m = re.search(r"\b\d{6}\b", plat["last"]["text"]) if plat else None
        return m.group(0) if m else None
    code = wait(latest_code, "the sign-in code on the owner's phone")
    step(f"the code on the phone: {code}")
    tok = c.post(f"{api}/auth/otp/verify", json={"phone": a.owner, "code": code}).json()
    token = tok.get("access_token")
    assert token, tok
    step("signed in")
    h = {"Authorization": f"Bearer {token}"}

    print("2. A shop is created")
    shop = c.post(f"{api}/onboarding/business", headers=h, json={"name": "Simulated Silks", "owner_name": "Owner"}).json()
    h = {"Authorization": f"Bearer {shop.get('access_token', token)}"}
    step(f"shop created: {shop.get('business_id', '?')}")
    p = c.post(f"{api}/products", headers=h, json={"name": "Banarasi Silk Saree", "description": "Pure silk", "category": "sarees", "variants": [
        {"name": "Red", "availability": "in_stock", "policy": {"disclosure": "fixed", "list_price": "8500", "negotiable": False, "ai_may_negotiate": False,
                                                              "concession_steps": 0, "round_to": "10"}}]})
    step(f"product added: {p.status_code}")

    print("3. The shop's number is connected through the sign-up popup of the simulator (the product exchanges the code with Meta's API)")
    made = c.post(f"{sim}/sim/api/signup/create", json={"name": "Simulated Silks", "display": "+91 97000 10101", "coexistence": False}).json()
    r = c.post(f"{api}/numbers/connect", headers=h, json={"code": made["code"], "waba_id": made["waba_id"], "phone_number_id": made["phone_number_id"], "coexistence": False})
    step(f"connect: {r.status_code} {r.text[:120]}")
    assert r.status_code in (200, 201, 202), r.text
    r = c.post(f"{api}/onboarding/go-live", headers=h)
    step(f"go live: {r.status_code} {r.text[:120]}")

    print("4. A customer on another phone chats with the shop's number")
    pnid = made["phone_number_id"]

    def say(text: str) -> None:
        c.post(f"{sim}/sim/api/customer/send", json={"pnid": pnid, "customer": a.customer, "text": text, "name": "Customer"})

    def replies() -> list[str]:
        ch = next((x for x in c.get(f"{sim}/sim/api/phone/{cust_wa}").json()["chats"] if x["pnid"] == pnid), None)
        msgs = [m for m in c.get(f"{sim}/sim/api/state").json()["messages"] if m["pnid"] == pnid and m["customer"] == cust_wa and m["direction"] != "in"]
        return [m["text"] for m in msgs] if ch else []
    say("hi, price of the red banarasi saree?")
    got = wait(replies, "the assistant's reply on the customer's phone", 60)
    for t in got:
        step(f"shop -> customer: {t[:140]}")
    audit = c.get(f"{sim}/sim/api/audit", params={"phone": cust_wa}).json()["summary"]
    print("5. Audit of the customer's phone:", audit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
