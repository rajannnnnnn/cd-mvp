"""Independent Meta simulator + WhatsApp-style test frontend.

It plays Meta toward the product, and plays the customer's phone and the owner's Business app toward a human tester:
  * Webhooks  (sim -> product):  signed POSTs to the product's webhook URL, exactly like Meta (X-Hub-Signature-256, batching not used).
  * Graph API (product -> sim):  POST /{version}/{phone_number_id}/messages and friends, answered with Meta's response/error shapes.
  * UI        (browser -> sim):  /sim/api/*  plus live updates over Server-Sent Events, and the static page at /.

The product is configured only with its normal settings: META_GRAPH_BASE=http://127.0.0.1:<port>, a business access token, and the
webhook callback URL set here. Nothing in the product knows this tool exists, and this tool imports nothing from the product.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from metasim import payloads as pl

STATIC = Path(__file__).parent / "static"
WINDOW_S = 24 * 3600


@dataclass
class Biz:
    pnid: str
    waba_id: str
    display: str
    name: str
    token: str
    coexistence: bool = True
    kind: str = "business"          # "business" or "platform" (the sender of owner login codes)


@dataclass
class Msg:
    id: str
    pnid: str
    customer: str                   # wa_id (digits)
    direction: str                  # "in" customer->business, "out" business->customer (API), "echo" business app->customer
    kind: str
    text: str
    ts: float
    status: str = "sent"            # for "in": sent|delivered|read(by business). for "out"/"echo": accepted|sent|delivered|read|failed
    error: str | None = None
    reply_to: str | None = None
    t_ack_ms: int | None = None     # webhook acknowledgement time (for "in")
    marks: dict[str, float] = field(default_factory=dict)


@dataclass
class Settings:
    webhook_url: str = "http://127.0.0.1:8001/webhooks/whatsapp"
    app_secret: str = "dev-app-secret"
    verify_token: str = "dev-verify-token"
    sent_ms: list[int] = field(default_factory=lambda: [300, 800])
    delivered_ms: list[int] = field(default_factory=lambda: [800, 2500])
    read_ms: list[int] = field(default_factory=lambda: [2000, 9000])
    read_receipts: bool = True      # customer has read receipts on
    phone_offline: bool = False     # customer phone offline: sent, never delivered
    fail_next: list[int] = field(default_factory=list)   # error codes applied to the next outbound sends (consumed one per send)
    enforce_window: bool = True


class Sim:
    def __init__(self) -> None:
        self.biz: dict[str, Biz] = {}
        self.customers: dict[str, str] = {}               # wa_id -> display name
        self.msgs: list[Msg] = []
        self.wire: list[dict[str, Any]] = []
        self.settings = Settings()
        self.last_customer_msg: dict[tuple[str, str], float] = {}
        self.typing_until: dict[tuple[str, str], float] = {}
        self.subscribers: list[asyncio.Queue[str]] = []
        self.tasks: set[asyncio.Task[Any]] = set()
        self.client = httpx.AsyncClient(timeout=15)

    # ---------------------------------------------------------------- bus
    def publish(self, kind: str, data: Any) -> None:
        line = json.dumps({"type": kind, "data": data}, ensure_ascii=False, default=str)
        for q in list(self.subscribers):
            q.put_nowait(line)

    def spawn(self, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def log_wire(self, direction: str, **kw: Any) -> dict[str, Any]:
        e = {"id": uuid.uuid4().hex[:8], "t": time.time(), "dir": direction, **kw}
        self.wire.append(e)
        del self.wire[:-500]
        self.publish("wire", e)
        return e

    def msg_out(self, m: Msg) -> dict[str, Any]:
        d = asdict(m)
        return d

    # ---------------------------------------------------------------- webhooks (sim -> product)
    async def send_webhook(self, payload: dict[str, Any], label: str) -> tuple[int | None, int]:
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        sig = "sha256=" + hmac.new(self.settings.app_secret.encode(), raw, hashlib.sha256).hexdigest()
        code: int | None = None
        ms = 0
        err = None
        for attempt, wait in enumerate((0, 1, 5, 30), start=1):        # Meta retries a non-200 with decreasing frequency
            if wait:
                await asyncio.sleep(wait)
            t0 = time.perf_counter()
            try:
                r = await self.client.post(self.settings.webhook_url, content=raw, headers={"content-type": "application/json", "x-hub-signature-256": sig})
                code, err = r.status_code, None
            except httpx.HTTPError as e:
                code, err = None, f"{type(e).__name__}: {e}"
            ms = int((time.perf_counter() - t0) * 1000)
            self.log_wire("webhook_out", label=label, attempt=attempt, status=code, ms=ms, error=err, body=payload)
            if code == 200:
                break
        return code, ms

    # ---------------------------------------------------------------- status flow for outbound messages
    def rnd(self, pair: list[int]) -> float:
        return random.uniform(pair[0], pair[1]) / 1000

    async def run_status_flow(self, m: Msg, biz: Biz, fail_code: int | None) -> None:
        s = self.settings
        await asyncio.sleep(self.rnd(s.sent_ms))
        await self._status(m, biz, "failed" if fail_code else "sent", fail_code)
        if fail_code:
            return
        if s.phone_offline:
            return
        await asyncio.sleep(self.rnd(s.delivered_ms))
        await self._status(m, biz, "delivered")
        if s.read_receipts:
            await asyncio.sleep(self.rnd(s.read_ms))
            await self._status(m, biz, "read")

    async def _status(self, m: Msg, biz: Biz, state: str, fail_code: int | None = None) -> None:
        m.status, m.marks[state] = state, time.time()
        err = None
        if state == "failed" and fail_code:
            _, body = pl.graph_error(fail_code)
            err = {"code": fail_code, "title": body["error"]["message"].split(") ", 1)[-1], "details": body["error"]["error_data"]["details"]}
            m.error = f"{fail_code}: {err['title']}"
        self.publish("message", self.msg_out(m))
        if biz.kind == "business":
            await self.send_webhook(pl.status(biz.waba_id, biz.display, biz.pnid, wamid=m.id, state=state, recipient=m.customer, ts=int(time.time()), error=err),
                                    f"status:{state}")


sim = Sim()
app = FastAPI(title="Meta simulator", docs_url="/sim/docs", openapi_url="/sim/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC), name="static")      # before the catch-all Graph routes below
VER = r"v\d+\.\d+"


def _default_state() -> None:
    sim.biz["sim-pn-1"] = Biz("sim-pn-1", "sim-waba-1", "+919700000001", "Sharma Sarees", "SIMTOKEN-business-1")
    sim.biz["sim-platform"] = Biz("sim-platform", "sim-waba-0", "+919700000000", "ChatDeal (platform)", "SIMTOKEN-platform", coexistence=False, kind="platform")
    sim.customers["919800001001"] = "Priya"
    sim.customers["919800001002"] = "Rahul"
    sim.customers["919800001003"] = "Anita"


_default_state()


def _auth(authorization: str | None, pnid: str) -> tuple[Biz | None, JSONResponse | None]:
    biz = sim.biz.get(pnid)
    if biz is None:
        body = {"error": {"message": "(#100) Unsupported post request. Object with ID does not exist", "type": "GraphMethodException", "code": 100, "fbtrace_id": "SIMx"}}
        return None, JSONResponse(body, status_code=400)
    tok = (authorization or "").removeprefix("Bearer ").strip()
    if tok != biz.token:
        http, body = pl.graph_error(190)
        return None, JSONResponse(body, status_code=http)
    return biz, None


# ===================================================================== Graph API (product -> sim)
@app.post("/{version}/{pnid}/messages")
async def graph_send(version: str, pnid: str, request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    if not re.fullmatch(VER, version):
        return JSONResponse({"error": {"message": "Unknown path components", "code": 2500}}, status_code=400)
    body = await request.json()
    biz, bad = _auth(authorization, pnid)
    sim.log_wire("api_in", method="POST", path=f"/{version}/{pnid}/messages", body=body, ok=bad is None)
    if bad is not None:
        return bad
    assert biz is not None

    if body.get("status") == "read":                                   # mark as read (+ typing indicator)
        mid = body.get("message_id")
        for m in sim.msgs:
            if m.id == mid and m.direction == "in":
                m.status, m.marks["read"] = "read", time.time()
                if body.get("typing_indicator"):
                    sim.typing_until[(pnid, m.customer)] = time.time() + 25
                sim.publish("message", sim.msg_out(m))
                sim.publish("typing", {"pnid": pnid, "customer": m.customer, "until": sim.typing_until.get((pnid, m.customer))})
                return JSONResponse({"success": True})
        http, err = pl.graph_error(131009)
        return JSONResponse(err, status_code=http)

    kind = body.get("type")
    to = pl.digits(str(body.get("to", "")))
    if not to or kind not in ("text", "reaction", "template", "interactive", "image", "audio", "document", "video", "sticker", "location", "contacts"):
        http, err = pl.graph_error(131009 if not to else 131051)
        return JSONResponse(err, status_code=http)
    s = sim.settings
    if s.fail_next:                                                   # injected failure
        code = s.fail_next.pop(0)
        sim.publish("settings", asdict(s))
        if code not in pl.ASYNC_FAILURES:
            http, err = pl.graph_error(code)
            sim.log_wire("api_out", status=http, body=err)
            return JSONResponse(err, status_code=http)
        fail_code: int | None = code
    else:
        fail_code = None
    last = sim.last_customer_msg.get((pnid, to))
    if kind != "template" and s.enforce_window and biz.kind == "business" and (last is None or time.time() - last > WINDOW_S):
        http, err = pl.graph_error(131047)
        sim.log_wire("api_out", status=http, body=err)
        return JSONResponse(err, status_code=http)

    if to not in sim.customers:
        sim.customers[to] = to
        sim.publish("customers", sim.customers)
    text = ""
    if kind == "text":
        text = (body.get("text") or {}).get("body", "")
    elif kind == "reaction":
        text = (body.get("reaction") or {}).get("emoji", "")
    elif kind == "template":
        text = f"[template {(body.get('template') or {}).get('name')}]"
    else:
        text = f"[{kind}]"
    m = Msg(pl.new_wamid(), pnid, to, "out", kind, text, time.time(), status="accepted", reply_to=(body.get("context") or {}).get("message_id"))
    sim.msgs.append(m)
    sim.typing_until.pop((pnid, to), None)
    sim.publish("message", sim.msg_out(m))
    sim.publish("typing", {"pnid": pnid, "customer": to, "until": None})
    resp = {"messaging_product": "whatsapp", "contacts": [{"input": str(body.get("to")), "wa_id": to}], "messages": [{"id": m.id}]}
    sim.log_wire("api_out", status=200, body=resp)
    sim.spawn(sim.run_status_flow(m, biz, fail_code))
    return JSONResponse(resp)


@app.get("/{version}/oauth/access_token")
async def graph_token(version: str, client_id: str = "", client_secret: str = "", code: str = "") -> JSONResponse:
    sim.log_wire("api_in", method="GET", path=f"/{version}/oauth/access_token", body={"client_id": client_id, "code": code})
    if not code.startswith("sim-code-"):
        return JSONResponse({"error": {"message": "This authorization code has expired or is invalid.", "type": "OAuthException", "code": 100}}, status_code=400)
    pnid = code.removeprefix("sim-code-")
    biz = sim.biz.get(pnid)
    return JSONResponse({"access_token": biz.token if biz else "SIMTOKEN-new", "token_type": "bearer"})


@app.post("/{version}/{waba}/subscribed_apps")
async def graph_subscribe(version: str, waba: str, authorization: str | None = Header(default=None)) -> JSONResponse:
    sim.log_wire("api_in", method="POST", path=f"/{version}/{waba}/subscribed_apps", body={})
    return JSONResponse({"success": True})


@app.get("/{version}/{pnid}")
async def graph_number(version: str, pnid: str, fields: str = "", authorization: str | None = Header(default=None)) -> JSONResponse:
    biz, bad = _auth(authorization, pnid)
    sim.log_wire("api_in", method="GET", path=f"/{version}/{pnid}?fields={fields}", body={}, ok=bad is None)
    if bad is not None:
        return bad
    assert biz is not None
    info = {"id": pnid, "display_phone_number": biz.display, "verified_name": biz.name, "quality_rating": "GREEN",
            "is_on_biz_app": biz.coexistence, "platform_type": "CLOUD_API"}
    want = [f.strip() for f in fields.split(",") if f.strip()]
    return JSONResponse({k: v for k, v in info.items() if not want or k in want or k == "id"})


@app.post("/{version}/{pnid}/register")
async def graph_register(version: str, pnid: str) -> JSONResponse:
    sim.log_wire("api_in", method="POST", path=f"/{version}/{pnid}/register", body={})
    return JSONResponse({"success": True})


# ===================================================================== UI API (browser -> sim)
@app.get("/sim/api/state")
async def ui_state() -> dict[str, Any]:
    return {"businesses": [asdict(b) for b in sim.biz.values()], "customers": sim.customers, "messages": [sim.msg_out(m) for m in sim.msgs[-600:]],
            "wire": sim.wire[-200:], "settings": asdict(sim.settings), "typing": {f"{k[0]}|{k[1]}": v for k, v in sim.typing_until.items()}}


@app.get("/sim/api/events")
async def ui_events(request: Request) -> StreamingResponse:
    q: asyncio.Queue[str] = asyncio.Queue()
    sim.subscribers.append(q)

    async def gen() -> Any:
        try:
            yield "retry: 1500\n\n"
            while not await request.is_disconnected():
                try:
                    line = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {line}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            sim.subscribers.remove(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache"})


@app.post("/sim/api/customer/send")
async def ui_customer_send(body: dict[str, Any]) -> dict[str, Any]:
    pnid, wa = body["pnid"], pl.digits(body["customer"])
    biz = sim.biz[pnid]
    kind = body.get("kind", "text")
    now = time.time()
    if wa not in sim.customers:
        sim.customers[wa] = body.get("name") or wa
        sim.publish("customers", sim.customers)
    m = Msg(pl.new_wamid(), pnid, wa, "in", kind, body.get("text") or body.get("emoji") or f"[{kind}]", now, status="sent", reply_to=body.get("reply_to"))
    sim.msgs.append(m)
    sim.last_customer_msg[(pnid, wa)] = now
    sim.publish("message", sim.msg_out(m))
    payload = pl.inbound_message(biz.waba_id, biz.display, pnid, from_wa=wa, name=sim.customers.get(wa), wamid=m.id, ts=int(now), kind=kind,
                                 text=body.get("text"), emoji=body.get("emoji"), reaction_to=body.get("reaction_to"),
                                 media_id="sim-media-" + uuid.uuid4().hex[:8] if kind in ("audio", "image") else None, reply_to=body.get("reply_to"))
    code, ms = await sim.send_webhook(payload, f"message:{kind}")
    m.t_ack_ms = ms
    if code == 200:
        m.status, m.marks["delivered"] = "delivered", time.time()
    sim.publish("message", sim.msg_out(m))
    return {"id": m.id, "ack_status": code, "ack_ms": ms}


@app.post("/sim/api/owner/send")
async def ui_owner_send(body: dict[str, Any]) -> dict[str, Any]:
    """The owner types in the WhatsApp Business app. Meta reports it to the product as an smb_message_echoes webhook (coexistence)."""
    pnid, to = body["pnid"], pl.digits(body["customer"])
    biz = sim.biz[pnid]
    now = time.time()
    m = Msg(pl.new_wamid(), pnid, to, "echo", "text", body["text"], now, status="sent")
    sim.msgs.append(m)
    sim.typing_until.pop((pnid, to), None)
    sim.publish("message", sim.msg_out(m))
    code, ms = await sim.send_webhook(pl.echo(biz.waba_id, biz.display, pnid, to_wa=to, wamid=m.id, ts=int(now), text=body["text"]), "echo")
    sim.spawn(_echo_ticks(m))
    return {"id": m.id, "ack_status": code, "ack_ms": ms}


async def _echo_ticks(m: Msg) -> None:
    await asyncio.sleep(sim.rnd(sim.settings.delivered_ms))
    m.status, m.marks["delivered"] = "delivered", time.time()
    sim.publish("message", sim.msg_out(m))
    if sim.settings.read_receipts:
        await asyncio.sleep(sim.rnd(sim.settings.read_ms))
        m.status, m.marks["read"] = "read", time.time()
        sim.publish("message", sim.msg_out(m))


@app.post("/sim/api/customers")
async def ui_add_customer(body: dict[str, Any]) -> dict[str, Any]:
    sim.customers[pl.digits(body["wa_id"])] = body.get("name") or body["wa_id"]
    sim.publish("customers", sim.customers)
    return sim.customers


@app.post("/sim/api/businesses")
async def ui_add_business(body: dict[str, Any]) -> dict[str, Any]:
    pnid = body.get("pnid") or "sim-pn-" + uuid.uuid4().hex[:6]
    sim.biz[pnid] = Biz(pnid, body.get("waba_id") or "sim-waba-" + uuid.uuid4().hex[:6], body["display"], body.get("name") or body["display"],
                        body.get("token") or "SIMTOKEN-" + uuid.uuid4().hex[:12], bool(body.get("coexistence", True)), body.get("kind", "business"))
    sim.publish("businesses", [asdict(b) for b in sim.biz.values()])
    return asdict(sim.biz[pnid])


@app.post("/sim/api/settings")
async def ui_settings(body: dict[str, Any]) -> dict[str, Any]:
    for k, v in body.items():
        if hasattr(sim.settings, k):
            setattr(sim.settings, k, v)
    sim.publish("settings", asdict(sim.settings))
    return asdict(sim.settings)


@app.post("/sim/api/webhook/verify")
async def ui_verify() -> dict[str, Any]:
    """What Meta does when you save the Callback URL: GET with hub.mode/hub.challenge/hub.verify_token, expect the challenge echoed with 200."""
    ch = str(random.randint(10**8, 10**9))
    try:
        r = await sim.client.get(sim.settings.webhook_url, params={"hub.mode": "subscribe", "hub.challenge": ch, "hub.verify_token": sim.settings.verify_token})
        ok = r.status_code == 200 and r.text.strip() == ch
        sim.log_wire("webhook_verify", status=r.status_code, ok=ok, body={"expected": ch, "got": r.text[:80]})
        return {"ok": ok, "status": r.status_code, "got": r.text[:80]}
    except httpx.HTTPError as e:
        sim.log_wire("webhook_verify", status=None, ok=False, body={"error": str(e)})
        return {"ok": False, "error": str(e)}


@app.post("/sim/api/account-event")
async def ui_account_event(body: dict[str, Any]) -> dict[str, Any]:
    biz = sim.biz[body["pnid"]]
    code, ms = await sim.send_webhook(pl.account_update(biz.waba_id, body["event"], int(time.time()),
                                                        **({"waba_info": {"waba_id": biz.waba_id, "owner_business_id": "sim-owner"},
                                                            "disconnection_info": {"reason": "PRIMARY_INACTIVITY", "initiated_by": "SYSTEM"}} if body["event"] == "PARTNER_REMOVED" else {})),
                                      f"account_update:{body['event']}")
    return {"ack_status": code, "ack_ms": ms}


@app.post("/sim/api/script")
async def ui_script(body: dict[str, Any]) -> dict[str, Any]:
    """Run a scripted customer: steps [{customer, pnid, text, wait_ms}] in order, with real waits, for burst / fragment tests."""
    async def run() -> None:
        for st in body["steps"]:
            await ui_customer_send({"pnid": st["pnid"], "customer": st["customer"], "text": st["text"]})
            await asyncio.sleep(st.get("wait_ms", 0) / 1000)
    sim.spawn(run())
    return {"started": len(body["steps"])}


@app.post("/sim/api/reset")
async def ui_reset() -> dict[str, Any]:
    sim.msgs.clear()
    sim.wire.clear()
    sim.last_customer_msg.clear()
    sim.typing_until.clear()
    sim.publish("reset", {})
    return {"ok": True}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
