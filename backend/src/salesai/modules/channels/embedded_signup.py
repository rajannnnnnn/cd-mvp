"""Embedded Signup completion (FR-ON-2). The browser runs Meta's Embedded Signup flow and hands us a short-lived
`code` plus the WABA and phone-number ids; this module finishes the job on the server with the Graph API:

  1. exchange the code for an access token (needs the app id and secret, which never reach the browser)
  2. subscribe our app to the customer's WhatsApp Business Account so its webhooks reach us
  3. read the number's display phone, verified name and quality rating
  4. register the number for the Cloud API (not for coexistence numbers, which stay on the Business app)

It returns what the caller needs to store the number. The token is never logged; failures carry a message that is
safe to show to the owner. Written from Meta's documentation; it runs against a local fake Graph server in tests and
must be verified against a real Meta test number before the first pilot (see docs/RUNNING.md)."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

import httpx


class SignupError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


@dataclass(frozen=True)
class SignupResult:
    access_token: str
    waba_id: str
    phone_number_id: str
    display_phone: str
    verified_name: str | None
    quality_rating: str | None
    coexistence: bool


def _graph_error(r: httpx.Response) -> str:
    try:
        err = (r.json() or {}).get("error") or {}
        return str(err.get("error_user_msg") or err.get("message") or r.text[:200])
    except ValueError:
        return r.text[:200]


async def complete_signup(*, graph_base: str, version: str, app_id: str, app_secret: str, code: str, waba_id: str,
                          phone_number_id: str, coexistence: bool, timeout_s: float = 15.0,
                          client: httpx.AsyncClient | None = None) -> SignupResult:
    base = f"{graph_base.rstrip('/')}/{version}"
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        async def call(method: str, url: str, step: str, **kw: Any) -> dict[str, Any]:
            try:
                r = await http.request(method, url, **kw)
            except httpx.HTTPError as e:
                raise SignupError("graph_unreachable", "We couldn't reach WhatsApp. Please try again in a minute.", retryable=True) from e
            if r.status_code >= 500:
                raise SignupError("graph_error", "WhatsApp had a problem. Please try again in a minute.", retryable=True)
            if r.status_code >= 400:
                raise SignupError(f"{step}_failed", _graph_error(r))
            return dict(r.json() or {})

        tok = await call("GET", f"{base}/oauth/access_token", "code_exchange",
                         params={"client_id": app_id, "client_secret": app_secret, "code": code})
        token = tok.get("access_token")
        if not token:
            raise SignupError("code_exchange_failed", "WhatsApp did not accept the sign-up. Please start again.")
        auth = {"Authorization": f"Bearer {token}"}

        await call("POST", f"{base}/{waba_id}/subscribed_apps", "subscribe", headers=auth)
        info = await call("GET", f"{base}/{phone_number_id}", "number_lookup", headers=auth,
                          params={"fields": "display_phone_number,verified_name,quality_rating"})
        display = str(info.get("display_phone_number") or "").strip()
        if not display:
            raise SignupError("number_lookup_failed", "We couldn't read the phone number from WhatsApp.")
        if not coexistence:
            await call("POST", f"{base}/{phone_number_id}/register", "register", headers=auth,
                       json={"messaging_product": "whatsapp", "pin": f"{secrets.randbelow(10**6):06d}"})
        rating = info.get("quality_rating")
        return SignupResult(token, waba_id, phone_number_id, display, info.get("verified_name"),
                            str(rating).lower() if rating else None, coexistence)
    finally:
        if own:
            await http.aclose()
