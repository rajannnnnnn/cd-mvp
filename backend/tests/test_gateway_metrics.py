"""Channel gateway (ADR 0020 follow-up 4): `inbound` and `send` run as workers and expose health and metrics for the monitor
(queue depth and the age of the oldest job are what they scale on), and nothing else: no webhook endpoint, no API."""
from __future__ import annotations

import httpx

from salesai.__main__ import health_app


def _client(rt, role="worker"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=health_app(rt, role)), base_url="http://worker")


async def test_a_gateway_worker_exposes_queue_metrics_for_every_queue(rt):
    async with _client(rt) as c:
        r = await c.get("/metrics")
    assert r.status_code == 200
    for queue in ("inbound.events", "outbound.actions"):
        for metric in ("salesai_queue_depth", "salesai_queue_oldest_age_seconds", "salesai_queue_dead"):
            assert f'{metric}{{queue="{queue}"}}' in r.text


async def test_a_gateway_worker_answers_health_and_readiness(rt):
    async with _client(rt) as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/readyz")).status_code == 200


async def test_a_gateway_worker_serves_no_webhook_or_api_routes(rt):
    async with _client(rt) as c:
        assert (await c.get("/webhooks/whatsapp")).status_code == 404
        assert (await c.post("/webhooks/whatsapp", content=b"{}")).status_code == 404
        assert (await c.get("/v1/business")).status_code == 404
