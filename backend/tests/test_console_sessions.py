"""ADR 0048: every console sign-in is recorded (where, on what, how, for how long) and shown to the founder; ending someone else's session needs a fresh
authenticator code; a new browser or network that needed the second layer raises an alert."""
from __future__ import annotations

from tests.admin_helpers import A1, admin_login, code_for, forget_codes, make_admin, sign_in
from tests.test_founder_sign_in import another_browser, credentials, fresh_step

CHROME_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
EDGE_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"


def as_(ua: str, ip: str, **more) -> dict:
    return {"User-Agent": ua, "X-Forwarded-For": ip, **more}


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def sessions(client, tokens: dict, **kw) -> list[dict]:
    r = await client.get(f"{A1}/auth/sessions", headers={**bearer(tokens), **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def events(client, tokens: dict) -> list[dict]:
    r = await client.get(f"{A1}/auth/events", headers=bearer(tokens))
    assert r.status_code == 200, r.text
    return r.json()


async def sign_in_from(world, browser, phone: str, secret: str, ua: str, ip: str, **more) -> dict:
    """A full sign-in from another browser, as the founder (or an attacker holding the secret) would do it."""
    await fresh_step(world)
    await forget_codes(world)
    return await admin_login(browser, world.rt, phone, secret, headers=as_(ua, ip, **more))


# ---------------------------------------------------------------- what is recorded

async def test_a_sign_in_records_where_what_and_how(world, client):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret, headers=as_(CHROME_WIN, "203.0.113.9"))
    [s] = await sessions(client, tokens)
    assert s["status"] == "active" and s["is_current"] is True
    assert s["device"] == "Chrome 142 on Windows" and s["user_agent"] == CHROME_WIN
    assert s["ip"] == "203.0.113.9" and s["network"] == "203.0.113.0/24" and s["where"] == "203.0.113.9"
    assert s["method"] == "authenticator+whatsapp+email" and s["risk"] == "high" and s["step_up_reason"] == "first_sign_in"      # the first sign-in: both extra codes
    assert s["started_at"] and s["last_seen_at"] and s["expires_at"] and s["duration_s"] >= 0 and s["ended_at"] is None and s["browser_id"]


async def test_a_routine_sign_in_says_it_needed_only_the_authenticator(world, client):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret, headers=as_(CHROME_WIN, "203.0.113.9"))
    await fresh_step(world)
    second = (await sign_in(client, world.rt, secret, headers=as_(CHROME_WIN, "203.0.113.9"))).json()
    rows = await sessions(client, second, **as_(CHROME_WIN, "203.0.113.9"))
    assert [r["method"] for r in rows] == ["authenticator", "authenticator+whatsapp+email"]               # newest first
    assert rows[0]["step_up_reason"] is None and rows[0]["usual_network"] is True and rows[0]["browser_id"] == rows[1]["browser_id"]    # the same browser


async def test_the_same_browser_has_the_same_id_and_another_browser_does_not(world, client):
    phone, secret = await make_admin(world)
    mine = await admin_login(client, world.rt, phone, secret, headers=as_(CHROME_WIN, "203.0.113.9"))
    async with another_browser(client) as other:
        theirs = await sign_in_from(world, other, phone, secret, FIREFOX_LINUX, "198.51.100.77")
    rows = await sessions(client, mine, **as_(CHROME_WIN, "203.0.113.9"))
    assert len({r["browser_id"] for r in rows}) == 2
    attacker = next(r for r in rows if r["ip"] == "198.51.100.77")
    assert attacker["device"] == "Firefox 130 on Linux" and attacker["usual_network"] is False and attacker["step_up_reason"] in ("new_device", "new_network")
    assert theirs["access_token"]


async def test_a_countrys_code_is_shown_only_when_the_proxy_is_configured_to_send_one(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret, headers=as_(CHROME_WIN, "203.0.113.9", **{"CF-IPCountry": "IN"}))
    assert (await sessions(client, tokens))[0]["country"] is None                                    # not configured: the header is ignored
    monkeypatch.setattr(world.rt.settings, "admin_geo_header", "CF-IPCountry")
    async with another_browser(client) as other:
        t2 = await sign_in_from(world, other, phone, secret, SAFARI_IPHONE, "198.51.100.5", **{"CF-IPCountry": "in"})
        t3 = await sign_in_from(world, other, phone, secret, EDGE_MAC, "198.51.100.6", **{"CF-IPCountry": "<script>"})
    rows = {r["ip"]: r for r in await sessions(client, tokens)}
    assert rows["198.51.100.5"]["country"] == "IN" and rows["198.51.100.5"]["where"] == "IN · 198.51.100.5" and rows["198.51.100.5"]["device"] == "Safari 17 on iPhone"
    assert rows["198.51.100.6"]["country"] is None                                                   # junk in the header is never shown
    assert t2["access_token"] and t3["access_token"]


async def test_last_use_is_written_at_most_once_a_minute(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    first = (await sessions(client, tokens))[0]["last_seen_at"]
    assert (await sessions(client, tokens))[0]["last_seen_at"] == first                              # a second later: not rewritten
    monkeypatch.setattr("salesai.modules.auth.admin.SEEN_EVERY_S", 0)
    await sessions(client, tokens)
    assert (await sessions(client, tokens))[0]["last_seen_at"] >= first


# ---------------------------------------------------------------- the attempts, and being told

async def test_refused_attempts_are_listed_with_where_and_what(world, client):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    bad = await client.post(f"{A1}/auth/sign-in", json={"code": "000000"}, headers=as_(FIREFOX_LINUX, "198.51.100.99"))
    assert bad.status_code == 401
    failed = [e for e in await events(client, tokens) if e["action"] == "admin.sign_in_failed"]
    assert failed and failed[0]["detail"]["ip"] == "198.51.100.99" and failed[0]["detail"]["device"] == "Firefox 130 on Linux"
    assert failed[0]["detail"]["step"] == "authenticator" and failed[0]["detail"]["where"] == "198.51.100.99"
    assert "000000" not in str(failed[0])                                                            # never the code that was tried


async def test_a_sign_in_from_a_new_browser_that_needed_whatsapp_raises_a_high_alert_with_where_and_what(world, client):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret, headers=as_(CHROME_WIN, "203.0.113.9"))      # the first sign-in is expected: no alert
    async with world.rt.db.system_tx() as c:
        assert await (await c.execute("SELECT 1 FROM operator_alerts WHERE kind='admin_new_sign_in'")).fetchone() is None
    async with another_browser(client) as other:
        await sign_in_from(world, other, phone, secret, FIREFOX_LINUX, "198.51.100.77")
    async with world.rt.db.system_tx() as c:
        a = await (await c.execute("SELECT severity, message FROM operator_alerts WHERE kind='admin_new_sign_in'")).fetchone()
    assert a is not None and a["severity"] == "high" and "Firefox 130 on Linux" in a["message"] and "198.51.100.77" in a["message"]


# ---------------------------------------------------------------- ending sessions needs the authenticator

async def _two_sessions(world, client, phone, secret):
    mine = await admin_login(client, world.rt, phone, secret, headers=as_(CHROME_WIN, "203.0.113.9"))
    other = another_browser(client)
    theirs = await sign_in_from(world, other, phone, secret, FIREFOX_LINUX, "198.51.100.77")
    rows = await sessions(client, mine, **as_(CHROME_WIN, "203.0.113.9"))
    return mine, other, theirs, next(r for r in rows if r["ip"] == "198.51.100.77")


async def test_ending_another_session_needs_a_fresh_authenticator_code(world, client):
    phone, secret = await make_admin(world)
    mine, other, theirs, target = await _two_sessions(world, client, phone, secret)
    url = f"{A1}/auth/sessions/{target['id']}/revoke"
    assert (await client.post(url, headers=bearer(mine), json={"code": "000000"})).status_code == 401           # wrong code: refused
    assert (await client.post(url, headers=bearer(mine), json={})).status_code == 422                            # no code: refused
    assert (await client.post(url, headers=bearer(theirs), json={"code": "000000"})).status_code == 401           # the attacker's own token changes nothing
    assert (await other.get(f"{A1}/auth/me", headers=bearer(theirs))).status_code == 200                         # still there
    await fresh_step(world)
    assert (await client.post(url, headers=bearer(mine), json={"code": code_for(world.rt, secret)})).status_code == 204
    assert (await other.get(f"{A1}/auth/me", headers=bearer(theirs))).status_code == 401                         # cut off at once
    assert (await client.get(f"{A1}/auth/me", headers=bearer(mine))).status_code == 200                          # the founder's own session is untouched
    rows = {r["id"]: r for r in await sessions(client, mine, **as_(CHROME_WIN, "203.0.113.9"))}
    assert rows[target["id"]]["status"] == "ended" and rows[target["id"]]["ended_reason"] == "revoked" and rows[target["id"]]["ended_at"]
    assert any(e["action"] == "admin.session_revoked" and e["detail"]["session"] == target["id"] for e in await events(client, mine))
    await other.aclose()


async def test_a_code_used_to_end_a_session_cannot_be_used_again_and_wrong_ones_count_toward_the_lock(world, client):
    phone, secret = await make_admin(world)
    mine, other, _, target = await _two_sessions(world, client, phone, secret)
    url = f"{A1}/auth/sessions/{target['id']}/revoke"
    await fresh_step(world)
    code = code_for(world.rt, secret)
    assert (await client.post(url, headers=bearer(mine), json={"code": code})).status_code == 204
    assert (await client.post(url, headers=bearer(mine), json={"code": code})).status_code == 401              # a replay of the same code
    for _ in range(5):
        await client.post(url, headers=bearer(mine), json={"code": "000000"})
    assert (await client.post(url, headers=bearer(mine), json={"code": code_for(world.rt, secret, steps_ahead=1)})).status_code == 429
    await other.aclose()


async def test_ending_a_session_also_makes_that_browser_ask_for_whatsapp_again(world, client):
    phone, secret = await make_admin(world)
    mine, other, _, target = await _two_sessions(world, client, phone, secret)
    await fresh_step(world)
    assert (await client.post(f"{A1}/auth/sessions/{target['id']}/revoke", headers=bearer(mine), json={"code": code_for(world.rt, secret)})).status_code == 204
    await fresh_step(world)
    again = (await sign_in(other, world.rt, secret, headers=as_(FIREFOX_LINUX, "198.51.100.77"))).json()
    assert again["step_up"] == "codes"                                                                   # its cookie no longer skips the second layer
    await other.aclose()


async def test_ending_a_session_can_keep_its_browser_trusted_if_asked(world, client):
    phone, secret = await make_admin(world)
    mine, other, _, target = await _two_sessions(world, client, phone, secret)
    await fresh_step(world)
    r = await client.post(f"{A1}/auth/sessions/{target['id']}/revoke", headers=bearer(mine), json={"code": code_for(world.rt, secret), "forget_browser": False})
    assert r.status_code == 204
    await fresh_step(world)
    assert "access_token" in (await sign_in(other, world.rt, secret, headers=as_(FIREFOX_LINUX, "198.51.100.77"))).json()
    await other.aclose()


async def test_ending_every_other_session_keeps_this_one_and_its_browser(world, client):
    phone, secret = await make_admin(world)
    mine, other, theirs, _ = await _two_sessions(world, client, phone, secret)
    await fresh_step(world)
    r = await client.post(f"{A1}/auth/sessions/revoke-others", headers=bearer(mine), json={"code": code_for(world.rt, secret)})
    assert r.status_code == 200 and r.json() == {"ended": 1}
    assert (await other.get(f"{A1}/auth/me", headers=bearer(theirs))).status_code == 401
    assert (await client.get(f"{A1}/auth/me", headers=bearer(mine))).status_code == 200
    assert len((await credentials(world))["trusted_devices"]) == 1                                               # only this browser is still trusted
    await other.aclose()


async def test_signing_out_everywhere_ends_every_session_and_forgets_every_browser(world, client):
    phone, secret = await make_admin(world)
    mine, other, theirs, _ = await _two_sessions(world, client, phone, secret)
    await fresh_step(world)
    r = await client.post(f"{A1}/auth/sessions/revoke-others", headers=bearer(mine), json={"code": code_for(world.rt, secret), "include_this_session": True})
    assert r.status_code == 200 and r.json() == {"ended": 2}
    for c, t in ((client, mine), (other, theirs)):
        assert (await c.get(f"{A1}/auth/me", headers=bearer(t))).status_code == 401
    assert (await credentials(world))["trusted_devices"] == []
    await other.aclose()


async def test_signing_out_ends_only_this_session(world, client):
    phone, secret = await make_admin(world)
    mine, other, theirs, _ = await _two_sessions(world, client, phone, secret)
    assert (await client.post(f"{A1}/auth/logout", headers=bearer(mine))).status_code == 204                     # one's own sign-out needs no code
    assert (await client.get(f"{A1}/auth/me", headers=bearer(mine))).status_code == 401
    assert (await other.get(f"{A1}/auth/me", headers=bearer(theirs))).status_code == 200                          # the other one carries on
    rows = await sessions(other, theirs, **as_(FIREFOX_LINUX, "198.51.100.77"))
    assert {r["ended_reason"] for r in rows if r["status"] == "ended"} == {"signed_out"}
    await other.aclose()


async def test_an_expired_session_is_shown_as_expired_and_extending_is_shown_as_extended(world, client):
    phone, secret = await make_admin(world)
    mine, other, _, target = await _two_sessions(world, client, phone, secret)
    async with world.rt.db.console_tx() as c:
        await c.execute("UPDATE admin_sessions SET expires_at = now() - interval '5 minutes' WHERE id=%s", (target["id"],))
    await fresh_step(world)
    ext = await client.post(f"{A1}/auth/extend", headers=bearer(mine), json={"code": code_for(world.rt, secret)})
    assert ext.status_code == 200
    rows = await sessions(client, ext.json(), **as_(CHROME_WIN, "203.0.113.9"))
    by = {r["id"]: r for r in rows}
    assert by[target["id"]]["status"] == "expired" and by[target["id"]]["ended_reason"] == "expired"
    assert any(r["status"] == "ended" and r["ended_reason"] == "extended" for r in rows) and any(r["method"] == "extended" and r["status"] == "active" for r in rows)
    await other.aclose()


async def test_the_security_screens_need_a_session_and_do_not_exist_on_the_public_app(world, client, rt):
    import httpx

    from salesai.api.app import create_app
    for path in ("sessions", "events"):
        assert (await client.get(f"{A1}/auth/{path}")).status_code == 401
    assert (await client.post(f"{A1}/auth/sessions/revoke-others", json={"code": "000000"})).status_code == 401
    web = create_app(rt, "web")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url="http://test") as c:
        assert (await c.get(f"{A1}/auth/sessions")).status_code == 404
