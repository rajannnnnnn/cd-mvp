"""Generic JSON webhook sink. The body carries a `text` field, which Slack, Discord-compatible and Teams-style incoming
webhooks accept as is, plus structured fields for anything that parses them."""
from __future__ import annotations

from typing import Any

import httpx


class WebhookSink:
    name = "webhook"
    levels = frozenset({"high", "critical"})

    def __init__(self, url: str, *, timeout_s: float = 5.0, client: httpx.AsyncClient | None = None):
        self.url = url
        self._client = client
        self.timeout_s = timeout_s

    async def send(self, alert: dict[str, Any], text: str) -> None:
        body = {"text": text, "id": alert["id"], "severity": alert["severity"], "kind": alert["kind"],
                "business": alert.get("business_name"), "created_at": alert["created_at"].isoformat(),
                "occurrences": alert.get("occurrences", 1), "recovered": bool(alert.get("recovered"))}
        client = self._client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            r = await client.post(self.url, json=body)
            r.raise_for_status()
        finally:
            if self._client is None:
                await client.aclose()


async def ping_outside(url: str, component: str, *, timeout_s: float = 5.0) -> bool:
    """Post to an outside dead-man's switch (ADR 0046). True when it answered 2xx; any failure is the caller's to log."""
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, json={"service": "chatdeal", "component": component})
        return r.is_success
    except httpx.HTTPError:
        return False
