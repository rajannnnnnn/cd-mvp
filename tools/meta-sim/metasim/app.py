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
import base64
import csv
import hashlib
import hmac
import io
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
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from metasim import payloads as pl

STATIC = Path(__file__).parent / "static"
# What Meta would hold: the approved templates (name -> language -> body with {{1}} placeholders). Editable data, not product code.
TEMPLATES: dict[str, dict[str, str]] = json.loads((Path(__file__).parent / "templates.json").read_text(encoding="utf-8"))
_PH = re.compile(r"\{\{(\d+)\}\}")


def render_template(name: str, language: str, params: list[str]) -> str | None:
    body = (TEMPLATES.get(name) or {}).get(language) or (TEMPLATES.get(name) or {}).get("en")
    return None if body is None else _PH.sub(lambda m: params[int(m.group(1)) - 1] if int(m.group(1)) <= len(params) else "", body)
WINDOW_S = 24 * 3600
PERSISTED = {"message", "customers", "businesses", "settings", "reset"}      # the events after which the state is saved
SECRET_SETTINGS = {"app_secret", "verify_token", "webhook_url"}                # set from the environment at every start, never read back from the file


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
    template: str | None = None
    media_id: str | None = None


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
        self.media: dict[str, tuple[bytes, str]] = {}
        self.state_file: Path | None = None                # set at start-up (SIM_STATE_FILE): where the phones, numbers and chats survive a restart
        self._save_timer: asyncio.TimerHandle | None = None

    # ---------------------------------------------------------------- bus
    def publish(self, kind: str, data: Any) -> None:
        line = json.dumps({"type": kind, "data": data}, ensure_ascii=False, default=str)
        for q in list(self.subscribers):
            q.put_nowait(line)
        if kind in PERSISTED and self.state_file is not None:
            self._dirty()

    # ---------------------------------------------------------------- keeping the phones, numbers and chats across a restart (QA should not have to set up again)
    def _dirty(self) -> None:
        """Save a moment after the last change, not on every one."""
        if self._save_timer is not None:
            return
        try:
            self._save_timer = asyncio.get_running_loop().call_later(1.0, self.save_state)
        except RuntimeError:
            self.save_state()

    def save_state(self) -> None:
        self._save_timer = None
        if self.state_file is None:
            return
        data = {"version": 1, "biz": [asdict(b) for b in self.biz.values()], "customers": self.customers, "msgs": [asdict(m) for m in self.msgs[-3000:]],
                "last_customer_msg": [[p, c, t] for (p, c), t in self.last_customer_msg.items()],
                "settings": {k: v for k, v in asdict(self.settings).items() if k not in SECRET_SETTINGS}}      # the secrets come from the environment on every start
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_file)                                                                          # never a half-written file

    def load_state(self) -> bool:
        if self.state_file is None or not self.state_file.exists():
            return False
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            biz = {b["pnid"]: Biz(**b) for b in data["biz"]}
            msgs = [Msg(**m) for m in data["msgs"]]
        except (OSError, ValueError, KeyError, TypeError):
            return False                                                                                     # an unreadable file is ignored: start from the defaults
        self.biz, self.customers, self.msgs = biz, {str(k): v for k, v in data.get("customers", {}).items()}, msgs
        self.last_customer_msg = {(p, c): float(t) for p, c, t in data.get("last_customer_msg", [])}
        for k, v in (data.get("settings") or {}).items():
            if k not in SECRET_SETTINGS and hasattr(self.settings, k):
                setattr(self.settings, k, v)
        return True

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
        t = body.get("template") or {}
        params = [str(p.get("text", "")) for c in t.get("components") or [] for p in c.get("parameters") or []]
        text = render_template(str(t.get("name")), str((t.get("language") or {}).get("code") or "en"), params) or ""
        if not text:
            err = {"error": {"message": "(#132001) Template name does not exist in the translation", "type": "OAuthException", "code": 132001,
                             "error_data": {"messaging_product": "whatsapp", "details": f"template name ({t.get('name')}) does not exist in {(t.get('language') or {}).get('code')}"}, "fbtrace_id": "SIMx"}}
            sim.log_wire("api_out", status=404, body=err)
            return JSONResponse(err, status_code=404)
    else:
        text = f"[{kind}]"
    m = Msg(pl.new_wamid(), pnid, to, "out", kind, text, time.time(), status="accepted", reply_to=(body.get("context") or {}).get("message_id"),
            template=(body.get("template") or {}).get("name") if kind == "template" else None)
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
async def graph_number(version: str, pnid: str, request: Request, fields: str = "", authorization: str | None = Header(default=None)) -> JSONResponse:
    if pnid.startswith("sim-media-"):                                    # the product asks where a voice note or photo can be downloaded
        sim.log_wire("api_in", method="GET", path=f"/{version}/{pnid}", body={})
        if pnid not in sim.media:
            http, err = pl.graph_error(131009)
            return JSONResponse(err, status_code=http)
        return JSONResponse({"id": pnid, "url": f"{str(request.base_url).rstrip('/')}/sim/media/{pnid}", "mime_type": sim.media[pnid][1], "file_size": len(sim.media[pnid][0])})
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
    media_id = "sim-media-" + uuid.uuid4().hex[:8] if kind in ("audio", "image") else None
    if media_id:
        data = base64.b64decode(body["media_b64"]) if body.get("media_b64") else (b"" if kind == "audio" else b"\xff\xd8\xff\xd9")
        sim.media[media_id] = (data, body.get("mime") or ("audio/ogg; codecs=opus" if kind == "audio" else "image/jpeg"))
    m = Msg(pl.new_wamid(), pnid, wa, "in", kind, body.get("text") or body.get("emoji") or f"[{kind}]", now, status="sent", reply_to=body.get("reply_to"), media_id=media_id)
    sim.msgs.append(m)
    sim.last_customer_msg[(pnid, wa)] = now
    sim.publish("message", sim.msg_out(m))
    payload = pl.inbound_message(biz.waba_id, biz.display, pnid, from_wa=wa, name=sim.customers.get(wa), wamid=m.id, ts=int(now), kind=kind,
                                 text=body.get("text"), emoji=body.get("emoji"), reaction_to=body.get("reaction_to"),
                                 media_id=media_id, reply_to=body.get("reply_to"))
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


# ===================================================================== media the product downloads (voice notes, photos)
@app.get("/sim/media/{media_id}")
async def media_download(media_id: str, authorization: str | None = Header(default=None)) -> Response:
    sim.log_wire("api_in", method="GET", path=f"/sim/media/{media_id}", body={})
    if media_id not in sim.media or not (authorization or "").removeprefix("Bearer ").strip().startswith("SIMTOKEN"):
        return JSONResponse({"error": {"message": "Media not found or token invalid", "code": 100}}, status_code=404)
    data, mime = sim.media[media_id]
    return Response(data, media_type=mime.split(";")[0])


# ===================================================================== a phone: every chat this number has, with the shop and the platform sender
@app.get("/sim/api/phone/{wa}")
async def ui_phone(wa: str) -> dict[str, Any]:
    wa = pl.digits(wa)
    chats = []
    for b in sim.biz.values():
        ms = sorted((m for m in sim.msgs if m.pnid == b.pnid and m.customer == wa), key=lambda m: m.ts)
        last = ms[-1] if ms else None
        chats.append({"pnid": b.pnid, "name": b.name, "display": b.display, "kind": b.kind, "messages": len(ms),
                      "last": {"text": last.text, "ts": last.ts, "direction": last.direction} if last else None})
    chats.sort(key=lambda c: -(c["last"]["ts"] if c["last"] else 0))
    return {"wa": wa, "name": sim.customers.get(wa), "chats": chats}


# ===================================================================== Meta's sign-up popup, played from outside the product
# The product loads the browser script from META_SDK_URL and accepts the popup's messages from META_SIGNUP_ORIGINS; pointing both at this
# server is all it takes. The popup creates the shop's WhatsApp number here, then answers like Meta's Embedded Signup: a WA_EMBEDDED_SIGNUP
# message with the account and number ids, and an authorization code that the product exchanges at /oauth/access_token.
SDK_JS = """(function(){
  var ORIGIN=(document.currentScript&&new URL(document.currentScript.src).origin)||location.origin;
  window.FB={init:function(o){this._opts=o},
    login:function(cb,opts){
      var w=window.open(ORIGIN+'/sim/app/signup','sim-signup','width=480,height=700');
      function onMsg(e){
        if(e.origin!==ORIGIN)return; var d=e.data||{};
        if(d.sim_code){window.removeEventListener('message',onMsg);cb({authResponse:{code:d.sim_code}})}
        else if(d.sim_cancel){window.removeEventListener('message',onMsg);cb({})}}
      window.addEventListener('message',onMsg);
      var t=setInterval(function(){if(w&&w.closed){clearInterval(t)}},800);}};
  if(window.fbAsyncInit)window.fbAsyncInit();
})();"""

POPUP = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>WhatsApp sign-up (simulated)</title>
<style>body{font:15px system-ui;margin:0;background:#f0f2f5;color:#111b21}main{max-width:420px;margin:24px auto;background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 4px #0002}
h1{font-size:18px;margin:0 0 4px}p{color:#667781;margin:0 0 14px}label{display:block;font-size:12px;color:#667781;margin:10px 0 3px}input{width:100%;font:inherit;padding:8px;border:1px solid #d1d7db;border-radius:8px;box-sizing:border-box}
button{font:inherit;padding:9px 14px;border-radius:8px;border:1px solid #d1d7db;background:#fff;cursor:pointer}button.p{background:#00a884;color:#fff;border-color:#00a884}.r{display:flex;gap:8px;margin-top:16px;justify-content:flex-end}
.note{font-size:12px;background:#fff8e1;border-radius:8px;padding:8px;margin-top:12px;color:#665}</style>
<main><h1>Connect your WhatsApp number</h1><p>Simulated Meta sign-up. This creates the number inside the stand-in, as Meta would for your business.</p>
<label>Business name shown to customers</label><input id=name value="Sharma Sarees">
<label>WhatsApp number of the business (any number you like)</label><input id=num value="+91 97000 00002">
<label><input type=checkbox id=co checked style="width:auto"> This number is also used in the WhatsApp Business app (coexistence)</label>
<div class=note>Nothing here is real: no verification, no message to your phone. The number only exists in the stand-in.</div>
<div class=r><button id=cancel>Cancel</button><button class=p id=go>Continue</button></div></main>
<script>
const $=id=>document.getElementById(id);
function post(m){ if(window.opener) window.opener.postMessage(m,'*'); }
$('cancel').onclick=()=>{post(JSON.stringify({type:'WA_EMBEDDED_SIGNUP',event:'CANCEL',data:{current_step:'start'}}));post({sim_cancel:true});window.close()};
$('go').onclick=async()=>{
  const r=await (await fetch('/sim/api/signup/create',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:$('name').value,display:$('num').value,coexistence:$('co').checked})})).json();
  post(JSON.stringify({type:'WA_EMBEDDED_SIGNUP',event:r.coexistence?'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING':'FINISH',data:{waba_id:r.waba_id,phone_number_id:r.phone_number_id}}));
  post({sim_code:r.code}); window.close()};
</script>"""


@app.get("/sdk.js")
async def sdk_js() -> Response:
    return Response(SDK_JS, media_type="application/javascript", headers={"Access-Control-Allow-Origin": "*"})   # like Meta's script: the page loads it cross-origin


@app.get("/sim/app/signup")
async def signup_popup() -> HTMLResponse:
    return HTMLResponse(POPUP)


@app.post("/sim/api/signup/create")
async def signup_create(body: dict[str, Any]) -> dict[str, Any]:
    display = body.get("display") or "+919700000099"
    existing = next((b for b in sim.biz.values() if pl.digits(b.display) == pl.digits(display) and b.kind == "business"), None)
    if existing is None:
        pnid = "sim-pn-" + uuid.uuid4().hex[:6]
        existing = Biz(pnid, "sim-waba-" + uuid.uuid4().hex[:6], display, body.get("name") or display, "SIMTOKEN-" + uuid.uuid4().hex[:12], bool(body.get("coexistence", True)))
        sim.biz[pnid] = existing
        sim.publish("businesses", [asdict(b) for b in sim.biz.values()])
    sim.log_wire("signup", label="embedded signup completed", body={"phone_number_id": existing.pnid, "waba_id": existing.waba_id, "display": existing.display})
    return {"phone_number_id": existing.pnid, "waba_id": existing.waba_id, "code": "sim-code-" + existing.pnid, "coexistence": existing.coexistence}


# ===================================================================== audit: what happened to each message, per phone
def _webhook_attempts() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for e in sim.wire:
        if e.get("dir") != "webhook_out":
            continue
        for en in (e.get("body") or {}).get("entry", []):
            for ch in en.get("changes", []):
                v = ch.get("value") or {}
                for x in [*v.get("messages", []), *v.get("statuses", [])]:
                    out.setdefault(x.get("id", ""), []).append({"label": e.get("label"), "attempt": e.get("attempt"), "status": e.get("status"), "ms": e.get("ms")})
    return out


def audit_rows(phone: str | None = None, pnid: str | None = None) -> list[dict[str, Any]]:
    wa = pl.digits(phone) if phone else None
    hooks = _webhook_attempts()
    rows = []
    for m in sorted(sim.msgs, key=lambda x: x.ts):
        if (wa and m.customer != wa) or (pnid and m.pnid != pnid):
            continue
        b = sim.biz.get(m.pnid)
        who = {"in": "phone -> business", "out": "business -> phone (API)", "echo": "business app -> phone"}.get(m.direction, m.direction)
        otp = bool(m.template and "otp" in m.template) or (b is not None and b.kind == "platform" and bool(re.search(r"\b\d{6}\b", m.text)) and m.direction == "out")
        rows.append({"time": m.ts, "phone": "+" + m.customer, "business": b.name if b else m.pnid, "business_number": b.display if b else "", "kind_of_business": b.kind if b else "",
                     "direction": who, "type": m.kind, "template": m.template, "text": m.text, "otp": otp, "status": m.status, "error": m.error,
                     "sent_at": m.marks.get("sent"), "delivered_at": m.marks.get("delivered"), "read_at": m.marks.get("read"), "product_ack_ms": m.t_ack_ms,
                     "webhooks_to_product": hooks.get(m.id, []) if m.direction == "in" else [], "id": m.id})
    return rows


def audit_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ins = [r for r in rows if r["direction"].startswith("phone")]
    acks = [r["product_ack_ms"] for r in ins if r["product_ack_ms"] is not None]
    lat = []
    for i in ins:
        nxt = next((r for r in rows if r["direction"].startswith("business") and r["business"] == i["business"] and r["time"] > i["time"]), None)
        if nxt:
            lat.append(nxt["time"] - i["time"])
    return {"messages": len(rows), "from_phone": len(ins), "to_phone": len(rows) - len(ins), "failed": sum(1 for r in rows if r["status"] == "failed"),
            "otp_codes_delivered": sum(1 for r in rows if r["otp"]), "avg_product_ack_ms": int(sum(acks) / len(acks)) if acks else None,
            "avg_first_reply_s": round(sum(lat) / len(lat), 1) if lat else None,
            "webhook_failures": sum(1 for r in ins for w in r["webhooks_to_product"] if w["status"] != 200)}


@app.get("/sim/api/audit")
async def ui_audit(phone: str | None = None, pnid: str | None = None) -> dict[str, Any]:
    rows = audit_rows(phone, pnid)
    return {"summary": audit_summary(rows), "rows": rows}


@app.get("/sim/api/audit.csv")
async def ui_audit_csv(phone: str | None = None, pnid: str | None = None) -> PlainTextResponse:
    rows = audit_rows(phone, pnid)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["time", "phone", "business", "business_number", "direction", "type", "template", "text", "otp", "status", "error", "sent_at", "delivered_at", "read_at", "product_ack_ms", "id"]
    w.writerow(cols)
    for r in rows:
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[c])) if c in ("time",) else (r[c] if r[c] is not None else "") for c in cols])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={"content-disposition": 'attachment; filename="meta-sim-audit.csv"'})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
