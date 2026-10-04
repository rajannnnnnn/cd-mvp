"""Webhook endpoint (Meta), health, readiness, metrics and public config."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from salesai import __version__
from salesai.api.deps import RT
from salesai.config import ALL_QUEUES
from salesai.modules.channels.ingress import accept_webhook, verify_handshake
from salesai.obs import QUEUE_DEAD, QUEUE_DEPTH, QUEUE_OLDEST_AGE

webhooks = APIRouter(tags=["webhooks"])
system = APIRouter(tags=["system"])
public = APIRouter(tags=["public"])


@webhooks.get("/webhooks/whatsapp", response_class=PlainTextResponse, include_in_schema=False)
async def verify(request: Request, rt: RT) -> Response:
    q = request.query_params
    ch = verify_handshake(rt.settings.meta_verify_token, q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"))
    return PlainTextResponse(ch if ch is not None else "forbidden", status_code=200 if ch is not None else 403)


@webhooks.post("/webhooks/whatsapp", include_in_schema=False)
async def receive(request: Request, rt: RT) -> Response:
    raw = await request.body()
    status, body = await accept_webhook(rt.db, rt.settings.meta_app_secret, raw, request.headers.get("x-hub-signature-256"))
    from fastapi.responses import JSONResponse
    return JSONResponse(body, status_code=status)


@system.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "alive", "version": __version__}


@system.get("/readyz", include_in_schema=False)
async def readyz(rt: RT) -> Response:
    from fastapi.responses import JSONResponse
    ok = await rt.db.ping()
    return JSONResponse({"status": "ready" if ok else "not_ready", "database": ok}, status_code=200 if ok else 503)


@system.get("/metrics", include_in_schema=False)
async def metrics(rt: RT) -> Response:
    for s in await rt.queue.stats(list(ALL_QUEUES)):
        QUEUE_DEPTH.labels(s.queue).set(s.pending)
        QUEUE_OLDEST_AGE.labels(s.queue).set(s.oldest_due_age_s)
        QUEUE_DEAD.labels(s.queue).set(s.dead)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@public.get("/public/config", summary="What the frontend needs to know before sign-in")
async def public_config(rt: RT) -> dict[str, Any]:
    return {"environment": rt.settings.env, "simulator_enabled": rt.settings.simulator_enabled, "otp_channel": rt.settings.otp_channel,
            "languages": ["en", "hi"], "version": __version__, "otp_accept_any": rt.settings.otp_accept_any, "payment_mode": rt.billing.provider.mode,
            "embedded_signup": ({"app_id": rt.settings.meta_app_id, "config_id": rt.settings.meta_config_id, "graph_version": rt.settings.meta_graph_version}
                                if rt.settings.meta_app_id and rt.settings.meta_config_id else None)}
