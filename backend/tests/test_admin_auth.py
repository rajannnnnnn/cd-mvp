"""ADR 0028: the founder's console is its own application with its own sign-in. A WhatsApp code alone never opens it; an
authenticator code is mandatory; the public application can neither mint nor accept its sessions."""
from __future__ import annotations

import os
import time
import uuid
from unittest import mock

import httpx
import jwt
import pytest
from cryptography.exceptions import InvalidTag

from salesai.api.app import create_app
from salesai.config import Settings
from salesai.modules.auth.admin import enroll_admin
from tests.admin_helpers import A1, admin_login, code_for, make_admin, ticket
from tests.test_api import V1, hdr, login


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def audit(world, action: str, actor=None):
    async with world.rt.db.system_tx() as c:
        return await (await c.execute("SELECT * FROM platform_audit_log WHERE action=%s ORDER BY id", (action,))).fetchall()


# ---------------------------------------------------------------- the sign-in

async def test_a_whatsapp_code_and_an_authenticator_code_open_the_console(world, client):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    assert tokens["role"] == "operator" and tokens["expires_in"] == world.rt.settings.admin_session_minutes * 60
    r = await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))
    assert r.status_code == 200
    assert (await client.get(f"{A1}/auth/me", headers=bearer(tokens))).json()["phone"] == phone
    assert any(a["actor_account_id"] for a in await audit(world, "admin.signed_in"))


async def test_the_whatsapp_code_alone_is_not_a_session(world, client):
    phone, _ = await make_admin(world)
    t = await ticket(client, phone)
    for headers in ({"Authorization": f"Bearer {t}"}, {}):
        assert (await client.get(f"{V1}/operator/businesses", headers=headers)).status_code in (401, 403)
    assert (await client.get(f"{A1}/auth/me", headers={"Authorization": f"Bearer {t}"})).status_code in (401, 403)


async def test_an_operator_who_signs_in_through_the_public_app_gets_no_operator_access(world, client):
    phone, _ = await make_admin(world)
    tokens = await login(client, phone)                                          # the public app's own sign-in
    assert tokens["role"] != "operator"
    assert (await client.get(f"{V1}/operator/businesses", headers=hdr(tokens))).status_code in (401, 403)


async def test_the_public_secret_cannot_forge_an_admin_session(world, client):
    phone, secret = await make_admin(world)
    real = await admin_login(client, world.rt, phone, secret)
    claims = jwt.decode(real["access_token"], options={"verify_signature": False})
    for key in (world.rt.settings.jwt_secret, world.rt.settings.otp_secret, world.rt.settings.admin_jwt_secret + "x"):
        forged = jwt.encode(claims, key, algorithm="HS256")
        assert (await client.get(f"{V1}/operator/businesses", headers={"Authorization": f"Bearer {forged}"})).status_code == 401
    claims.pop("aud", None)                                                       # right key, but not an admin-audience token
    assert (await client.get(f"{V1}/operator/businesses", headers={"Authorization": "Bearer " + jwt.encode(claims, world.rt.settings.admin_jwt_secret, algorithm="HS256")})).status_code == 401


async def test_an_admin_session_is_not_accepted_by_the_shop_api(world, client):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    for path in ("/products", "/business", "/conversations"):
        assert (await client.get(f"{V1}{path}", headers=bearer(tokens))).status_code in (401, 403, 404)
    assert (await client.get(f"{V1}/products", headers=bearer(tokens))).status_code == 401


async def test_only_an_enrolled_operator_can_get_a_code_and_nobody_can_tell_who_is(world, client):
    ordinary = f"+9192{uuid.uuid4().int % 10**8:08d}"
    unenrolled = f"+9192{uuid.uuid4().int % 10**8:08d}"
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Op','operator')", (unenrolled,))
    enrolled, _ = await make_admin(world)
    answers = []
    for phone in (ordinary, unenrolled, enrolled, f"+9192{uuid.uuid4().int % 10**8:08d}"):
        r = await client.post(f"{A1}/auth/code/request", json={"phone": phone})
        answers.append((r.status_code, r.json()))
    assert len({str(a) for a in answers}) == 1                                    # the same answer for everyone
    async with world.rt.db.system_tx() as c:
        sent = {r["phone"] for r in await (await c.execute("SELECT phone FROM otp_deliveries WHERE phone = ANY(%s)", ([ordinary, unenrolled, enrolled],))).fetchall()}
    assert sent == {enrolled.removeprefix("+")} or sent == {enrolled}              # a code goes only to an enrolled operator
    for phone in (ordinary, unenrolled):
        r = await client.post(f"{A1}/auth/code/verify", json={"phone": phone, "code": "123456"})
        assert r.status_code == 401


async def test_the_demo_setting_that_accepts_any_code_never_applies_here(world, client, monkeypatch):
    phone, _ = await make_admin(world)
    monkeypatch.setattr(world.rt.settings, "otp_accept_any", True)
    r = await client.post(f"{A1}/auth/code/verify", json={"phone": phone, "code": "000000"})
    assert r.status_code == 401


# ---------------------------------------------------------------- the authenticator code

async def test_a_wrong_authenticator_code_is_refused_and_five_lock_the_account(world, client):
    phone, secret = await make_admin(world)
    t = await ticket(client, phone)
    for _ in range(5):
        assert (await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": "000000"})).status_code == 401
    r = await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": code_for(world.rt, secret)})
    assert r.status_code == 429                                                   # even the right code is refused while locked
    t2 = await ticket(client, phone)
    assert (await client.post(f"{A1}/auth/totp", json={"ticket": t2, "code": code_for(world.rt, secret, steps_ahead=1)})).status_code == 429
    async with world.rt.db.console_tx() as c:                                      # the lock ends
        await c.execute("UPDATE admin_credentials SET locked_until = now() - interval '1 second' WHERE account_id=(SELECT id FROM accounts WHERE phone=%s)", (phone,))
    t3 = await ticket(client, phone)
    assert (await client.post(f"{A1}/auth/totp", json={"ticket": t3, "code": code_for(world.rt, secret, steps_ahead=1)})).status_code == 200
    assert await audit(world, "admin.locked") and await audit(world, "admin.sign_in_failed")


async def test_a_code_cannot_be_used_twice(world, client):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret)
    t = await ticket(client, phone)
    assert (await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": code_for(world.rt, secret)})).status_code == 401      # same 30 seconds: replay
    assert (await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": code_for(world.rt, secret, steps_ahead=1)})).status_code == 200


async def test_a_ticket_works_once(world, client):
    phone, secret = await make_admin(world)
    t = await ticket(client, phone)
    assert (await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": code_for(world.rt, secret)})).status_code == 200
    assert (await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": code_for(world.rt, secret, steps_ahead=1)})).status_code == 401


async def test_the_secret_is_not_stored_in_the_clear(world):
    phone, secret = await make_admin(world)
    async with world.rt.db.console_tx() as c:
        blob = (await (await c.execute("SELECT totp_secret_enc FROM admin_credentials WHERE account_id=(SELECT id FROM accounts WHERE phone=%s)", (phone,))).fetchone())["totp_secret_enc"]
    assert secret.encode() not in bytes(blob)
    import hashlib

    from salesai import crypto
    with pytest.raises(InvalidTag):                                               # the public secret is not the key
        crypto.decrypt(hashlib.sha256(world.rt.settings.jwt_secret.encode()).digest(), bytes(blob))


async def test_enrolling_again_replaces_the_secret_and_clears_a_lock(world, client):
    phone, old = await make_admin(world)
    new = await enroll_admin(world.rt.db, world.rt.settings, phone)
    assert new != old
    t = await ticket(client, phone)
    assert (await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": code_for(world.rt, old)})).status_code == 401
    assert (await client.post(f"{A1}/auth/totp", json={"ticket": t, "code": code_for(world.rt, new, steps_ahead=1)})).status_code == 200
    assert len(await audit(world, "admin.enrolled")) >= 2


async def test_only_an_operator_can_be_enrolled(world):
    ordinary = f"+9192{uuid.uuid4().int % 10**8:08d}"
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO accounts (phone, name) VALUES (%s,'Shop')", (ordinary,))
    with pytest.raises(ValueError):
        await enroll_admin(world.rt.db, world.rt.settings, ordinary)
    with pytest.raises(ValueError):
        await enroll_admin(world.rt.db, world.rt.settings, "+919000000000")      # no such account


# ---------------------------------------------------------------- the session

async def test_the_session_is_short_and_ends_at_once_on_sign_out(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    assert tokens["expires_in"] <= 8 * 3600
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 200
    assert (await client.post(f"{A1}/auth/logout", headers=bearer(tokens))).status_code == 204
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 401
    assert await audit(world, "admin.signed_out")
    tokens = await admin_login(client, world.rt, phone, secret, steps_ahead=1)
    later = time.time() + world.rt.settings.admin_session_minutes * 60 + 5
    monkeypatch.setattr(world.rt.admin_auth, "clock", lambda: later)
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 401


async def test_a_session_is_extended_only_by_a_fresh_authenticator_code(world, client):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    same_step = await client.post(f"{A1}/auth/extend", headers=bearer(tokens), json={"code": code_for(world.rt, secret)})
    assert same_step.status_code == 401
    r = await client.post(f"{A1}/auth/extend", headers=bearer(tokens), json={"code": code_for(world.rt, secret, steps_ahead=1)})
    assert r.status_code == 200
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(r.json()))).status_code == 200
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 401          # the old session is gone


async def test_an_account_that_loses_the_operator_role_loses_the_session_at_once(world, client):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE accounts SET platform_role=NULL WHERE phone=%s", (phone,))
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 401


# ---------------------------------------------------------------- where it may be reached from

async def test_an_address_allowlist_can_restrict_the_console(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    monkeypatch.setattr(world.rt.settings, "admin_allowed_ips", "203.0.113.0/24, 2001:db8::/32")
    outside = {**bearer(tokens), "X-Forwarded-For": "198.51.100.7"}
    inside = {**bearer(tokens), "X-Forwarded-For": "203.0.113.50"}
    assert (await client.get(f"{V1}/operator/businesses", headers=outside)).status_code == 403
    assert (await client.post(f"{A1}/auth/code/request", json={"phone": phone}, headers={"X-Forwarded-For": "198.51.100.7"})).status_code == 403
    assert (await client.get(f"{V1}/operator/businesses", headers=inside)).status_code == 200
    assert (await client.get(f"{V1}/operator/businesses", headers={**bearer(tokens), "X-Forwarded-For": "2001:db8::1"})).status_code == 200


# ---------------------------------------------------------------- the application itself

async def _get(app, path, method="GET", **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        return await c.request(method, path, **kw)


async def test_the_public_application_has_no_admin_surface(rt):
    app = create_app(rt, "web")
    for path in (f"{V1}/operator/businesses", f"{V1}/operator/alerts", f"{A1}/auth/code/request", f"{A1}/auth/me"):
        assert (await _get(app, path)).status_code == 404, path


async def test_the_admin_application_has_no_shop_surface_and_no_webhooks(rt):
    app = create_app(rt, "admin")
    assert (await _get(app, "/healthz")).status_code == 200
    assert (await _get(app, f"{A1}/auth/me")).status_code == 401                  # it exists, and it wants a session
    for path in (f"{V1}/auth/otp/request", f"{V1}/products", f"{V1}/sim/inbox", "/webhooks/whatsapp"):
        assert (await _get(app, path)).status_code == 404, path


async def test_admin_responses_cannot_be_cached_framed_or_sniffed(rt):
    r = await _get(create_app(rt, "admin"), f"{A1}/auth/me")
    h = r.headers
    assert "no-store" in h["cache-control"] and h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["referrer-policy"] == "no-referrer"


async def test_admin_cross_origin_calls_are_allowed_only_from_the_admin_origin(rt, monkeypatch):
    monkeypatch.setattr(rt.settings, "admin_allowed_origins", "https://admin.chatdeal.example")
    app = create_app(rt, "admin")
    ok = await _get(app, f"{A1}/auth/me", headers={"Origin": "https://admin.chatdeal.example"})
    bad = await _get(app, f"{A1}/auth/me", headers={"Origin": "https://app.chatdeal.example"})
    assert ok.headers.get("access-control-allow-origin") == "https://admin.chatdeal.example"
    assert "access-control-allow-origin" not in bad.headers


# ---------------------------------------------------------------- configuration

def _settings(**kw) -> Settings:
    """Settings as a container would see them: only what it is given, not what the rest of the suite put in this process."""
    base = dict(role="admin", master_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", jwt_secret="j" * 32, otp_secret="o" * 32,
                admin_jwt_secret="a" * 32, database_url="postgresql://x/x", pricing_database_url="postgresql://x/x",
                system_database_url="postgresql://x/x", console_database_url="postgresql://x/x", migration_database_url="postgresql://x/x")
    with mock.patch.dict(os.environ, {}, clear=True):
        return Settings(_env_file=None, **{**base, **kw})


def test_the_admin_part_needs_its_own_secret_and_it_must_differ_from_the_public_one():
    assert _settings().role == "admin"
    with pytest.raises(ValueError, match="ADMIN_JWT_SECRET"):
        _settings(admin_jwt_secret="")
    with pytest.raises(ValueError, match="ADMIN_JWT_SECRET"):
        _settings(admin_jwt_secret="short")
    with pytest.raises(ValueError, match="differ"):
        _settings(admin_jwt_secret="j" * 32)
    with pytest.raises(ValueError, match="ADMIN_SESSION_MINUTES"):
        _settings(admin_session_minutes=0)
    with pytest.raises(ValueError, match="ADMIN_SESSION_MINUTES"):
        _settings(admin_session_minutes=24 * 60)
    with pytest.raises(ValueError, match="ADMIN_ALLOWED_IPS"):
        _settings(admin_allowed_ips="not-an-address")


def test_the_public_part_no_longer_holds_the_master_key_or_the_admin_secret():
    s = _settings(role="api", master_key="", admin_jwt_secret="", simulator_enabled=False)                   # no master key, no admin secret: starts fine
    assert s.admin_jwt_secret == "" and s.master_key == ""
    with pytest.raises(ValueError, match="OTP_SECRET"):
        _settings(role="api", master_key="", admin_jwt_secret="", otp_secret="", simulator_enabled=False)


def test_production_refuses_a_placeholder_admin_secret_and_a_wildcard_origin():
    prod = dict(env="production", simulator_enabled=False, otp_channel="whatsapp_cloud", payment_provider="manual", platform_wa_access_token="EAAx",
                jwt_secret="j" * 40, otp_secret="o" * 40, admin_allowed_origins="https://admin.chatdeal.example")
    assert _settings(**prod, admin_jwt_secret="z" * 40).env == "production"
    with pytest.raises(ValueError, match="placeholder"):
        _settings(**prod, admin_jwt_secret="change-me-" + "x" * 30)
    with pytest.raises(ValueError, match="ADMIN_ALLOWED_ORIGINS"):
        _settings(**{**prod, "admin_allowed_origins": "*"}, admin_jwt_secret="z" * 40)
    with pytest.raises(ValueError, match="ADMIN_ALLOWED_ORIGINS"):
        _settings(**{**prod, "admin_allowed_origins": ""}, admin_jwt_secret="z" * 40)


async def test_the_other_parts_database_login_cannot_read_admin_secrets_or_sessions(pg_urls):
    """ADR 0043 at the database: app_system (web, gateway, agent, scheduler) is refused; app_console (the founder console) can read them."""
    import psycopg
    for table in ("admin_credentials", "admin_sessions", "break_glass_grants"):
        with psycopg.connect(pg_urls["system"], autocommit=True) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"SELECT 1 FROM {table} LIMIT 1")      # noqa: S608
        with psycopg.connect(pg_urls["console"], autocommit=True) as c:
            c.execute(f"SELECT 1 FROM {table} LIMIT 1")      # noqa: S608
    with psycopg.connect(pg_urls["console"], autocommit=True) as c:      # and it keeps everything the platform role has
        c.execute("SELECT 1 FROM businesses LIMIT 1")
