"""Authentication: number-centric one-time codes, rate limits, rotating refresh tokens, revocation."""
from __future__ import annotations

import re
import time
import uuid

import jwt
import pytest

from salesai.modules.auth import AuthError, AuthService


@pytest.fixture
async def auth(rt):
    return AuthService(rt.db, rt.channels, rt.settings)


async def otp_for(world, phone: str) -> str:
    from salesai.phone import normalize_phone
    phone = normalize_phone(phone)
    rows = [r for r in await world.sim_thread(phone, "platform") if r["direction"] == "to_user" and r["template_name"] == "otp_login"]
    return re.search(r"\b(\d{6})\b", rows[-1]["body"]).group(1)


async def login(world, auth, phone: str, **kw):
    await auth.request_otp(phone, kw.get("ip", "10.0.0.1"))
    return await auth.verify_otp(phone, await otp_for(world, phone), "10.0.0.1", "test-browser")


async def test_code_arrives_on_the_whatsapp_number_and_logs_the_owner_in(world, auth):
    shop = await world.make_shop()
    res = await auth.request_otp(shop.owner_phone, "10.0.0.1")
    assert res["status"] == "sent"
    code = await otp_for(world, shop.owner_phone)
    async with world.rt.db.system_tx() as c:                       # the code is never stored, only an HMAC
        rows = await (await c.execute("SELECT code_hash FROM otp_challenges WHERE phone=%s", (shop.owner_phone,))).fetchall()
    assert rows and all(code not in r["code_hash"] for r in rows)
    pair = await auth.verify_otp(shop.owner_phone, code, "10.0.0.1", "Chrome on Android")
    assert pair.role == "owner" and pair.business_id == shop.business_id
    p = await auth.authenticate(pair.access_token)
    assert p.business_id == shop.business_id and p.phone == shop.owner_phone


async def test_phone_formats_are_normalised(world, auth):
    shop = await world.make_shop(owner_phone="+919812345678")
    pair = await login(world, auth, "98123 45678")                # a plain Indian mobile number
    assert pair.business_id == shop.business_id


async def test_wrong_code_attempts_are_limited_and_codes_are_single_use(world, auth):
    shop = await world.make_shop()
    await auth.request_otp(shop.owner_phone, "10.0.0.2")
    code = await otp_for(world, shop.owner_phone)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(AuthError) as e:
            await auth.verify_otp(shop.owner_phone, wrong, None, None)
        assert e.value.code == "invalid_code"
    with pytest.raises(AuthError) as e:
        await auth.verify_otp(shop.owner_phone, code, None, None)   # even the right code is locked out now
    assert e.value.code == "locked"
    shop2 = await world.make_shop()
    await auth.request_otp(shop2.owner_phone, "10.0.0.3")
    c2 = await otp_for(world, shop2.owner_phone)
    await auth.verify_otp(shop2.owner_phone, c2, None, None)
    with pytest.raises(AuthError):
        await auth.verify_otp(shop2.owner_phone, c2, None, None)    # single use


async def test_expired_code_is_rejected(world, auth):
    shop = await world.make_shop()
    await auth.request_otp(shop.owner_phone, "10.0.0.4")
    code = await otp_for(world, shop.owner_phone)
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE otp_challenges SET expires_at = now() - interval '1 second' WHERE phone=%s", (shop.owner_phone,))
    with pytest.raises(AuthError) as e:
        await auth.verify_otp(shop.owner_phone, code, None, None)
    assert e.value.code == "invalid_code"


async def test_request_rate_limits_per_number_and_per_ip(world, auth):
    shop = await world.make_shop()
    async with world.rt.db.system_tx() as c:                         # bypass the 20s spacing by back-dating
        for _ in range(5):
            await c.execute("INSERT INTO otp_challenges (phone, code_hash, expires_at, ip, created_at) VALUES (%s,'x', now() + interval '5 minutes', %s, now() - interval '5 minutes')",
                            (shop.owner_phone, "10.9.9.9"))
    with pytest.raises(AuthError) as e:
        await auth.request_otp(shop.owner_phone, "10.9.9.8")
    assert e.value.code == "rate_limited" and e.value.status == 429
    async with world.rt.db.system_tx() as c:
        for i in range(30):
            await c.execute("INSERT INTO otp_challenges (phone, code_hash, expires_at, ip, created_at) VALUES (%s,'x', now() + interval '5 minutes', '10.7.7.7', now() - interval '5 minutes')", (f"+91970000{i:04d}",))
    other = await world.make_shop()
    with pytest.raises(AuthError) as e:
        await auth.request_otp(other.owner_phone, "10.7.7.7")
    assert e.value.code == "rate_limited"


async def test_unknown_numbers_get_the_same_response_and_no_message(world, auth):
    ghost = f"+9196{uuid.uuid4().int % 10**8:08d}"
    res = await auth.request_otp(ghost, "10.0.0.5")
    assert res["status"] == "sent"                                    # no account enumeration
    assert await world.sim_thread(ghost, "platform") == []
    with pytest.raises(AuthError) as e:
        await auth.verify_otp(ghost, "123456", None, None)
    assert e.value.code == "invalid_code"


async def test_number_without_a_business_cannot_sign_in(world, auth):
    async with world.rt.db.system_tx() as c:
        phone = f"+9195{uuid.uuid4().int % 10**8:08d}"
        await c.execute("INSERT INTO accounts (phone) VALUES (%s)", (phone,))
    await auth.request_otp(phone, "10.0.0.6")
    with pytest.raises(AuthError) as e:
        await auth.verify_otp(phone, await otp_for(world, phone), None, None)
    assert e.value.code == "no_business"


async def test_blocked_account_cannot_sign_in(world, auth):
    shop = await world.make_shop()
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE accounts SET status='blocked' WHERE id=%s", (shop.owner_account_id,))
    await auth.request_otp(shop.owner_phone, "10.0.0.7")
    assert await world.sim_thread(shop.owner_phone, "platform") == []
    with pytest.raises(AuthError):
        await auth.verify_otp(shop.owner_phone, "123456", None, None)


async def test_refresh_rotates_and_reuse_revokes_the_whole_family(world, auth):
    shop = await world.make_shop()
    p1 = await login(world, auth, shop.owner_phone)
    p2 = await auth.refresh(p1.refresh_token, None)
    assert p2.refresh_token != p1.refresh_token
    await auth.authenticate(p2.access_token)
    with pytest.raises(AuthError):
        await auth.refresh(p1.refresh_token, None)                    # replay of the old token = theft signal
    with pytest.raises(AuthError):
        await auth.refresh(p2.refresh_token, None)                    # ...so the legitimate descendant is revoked too
    with pytest.raises(AuthError) as e:
        await auth.authenticate(p2.access_token)
    assert e.value.code == "session_revoked"


async def test_logout_and_device_revocation_end_access_immediately(world, auth):
    shop = await world.make_shop()
    a = await login(world, auth, shop.owner_phone)
    b = await login(world, auth, shop.owner_phone)
    pa, _pb = await auth.authenticate(a.access_token), await auth.authenticate(b.access_token)
    devices = await auth.sessions(pa)
    assert len(devices) == 2 and sum(d["current"] for d in devices) == 1
    other = next(d for d in devices if not d["current"])
    assert await auth.revoke_family(pa, uuid.UUID(other["id"]))
    with pytest.raises(AuthError):
        await auth.authenticate(b.access_token)
    await auth.authenticate(a.access_token)
    await auth.logout(pa)
    auth._revoked_cache.clear()
    with pytest.raises(AuthError):
        await auth.authenticate(a.access_token)
    with pytest.raises(AuthError):
        await auth.refresh(a.refresh_token, None)


async def test_tampered_expired_and_wrong_type_tokens_are_rejected(world, auth, rt):
    shop = await world.make_shop()
    pair = await login(world, auth, shop.owner_phone)
    head, body, sig = pair.access_token.split(".")
    for bad in (f"{head}.{body}.{sig[:-2]}xx", "garbage", jwt.encode({"sub": "x"}, "other-secret-0123456789abcdefghijkl", algorithm="HS256")):
        with pytest.raises(AuthError):
            await auth.authenticate(bad)
    expired = jwt.encode({"sub": str(shop.owner_account_id), "sid": str(uuid.uuid4()), "role": "owner", "typ": "access", "exp": int(time.time()) - 5}, rt.settings.jwt_secret, algorithm="HS256")
    with pytest.raises(AuthError) as e:
        await auth.authenticate(expired)
    assert e.value.code == "token_expired"
    nonsession = jwt.encode({"sub": str(shop.owner_account_id), "sid": str(uuid.uuid4()), "role": "owner", "typ": "access", "exp": int(time.time()) + 60}, rt.settings.jwt_secret, algorithm="HS256")
    with pytest.raises(AuthError):
        await auth.authenticate(nonsession)                           # a validly signed token for a session that doesn't exist


async def test_one_number_two_businesses_uses_a_ticket_then_picks(world, auth):
    from salesai.modules.tenants import create_business
    a = await world.make_shop("Shop A")
    cb = await create_business(world.rt.db, world.s.master_key_bytes, name="Shop B", owner_phone=a.owner_phone, owner_name="Owner")
    await auth.request_otp(a.owner_phone, "10.0.0.8")
    res = await auth.verify_otp(a.owner_phone, await otp_for(world, a.owner_phone), None, "x")
    assert isinstance(res, dict) and res["choose_business"] and len(res["businesses"]) == 2
    with pytest.raises(AuthError):
        await auth.authenticate(res["ticket"])                        # a ticket is not an API credential
    pair = await auth.select_business(res["ticket"], cb.business_id, None)
    assert (await auth.authenticate(pair.access_token)).business_id == cb.business_id
    with pytest.raises(AuthError):
        await auth.select_business(res["ticket"], uuid.uuid4(), None)   # ticket already consumed


async def test_operator_login_and_impersonation_is_audited(world, auth):
    async with world.rt.db.system_tx() as c:
        phone = f"+9194{uuid.uuid4().int % 10**8:08d}"
        await c.execute("INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Ops','operator')", (phone,))
    op = await login(world, auth, phone)
    assert op.role == "operator" and op.business_id is None
    shop = await world.make_shop()
    imp = await auth.impersonate(await auth.authenticate(op.access_token), shop.business_id, None)
    p = await auth.authenticate(imp.access_token)
    assert p.business_id == shop.business_id and p.role == "owner" and p.impersonated_by is not None
    assert await world.q(shop, "SELECT 1 FROM audit_log WHERE action='operator.impersonation'")
