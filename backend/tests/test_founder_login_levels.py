"""ADR 0056: the founder is three deployment settings (phone, email, authenticator secret); a login proves as much as its risk needs (routine: the
authenticator; elevated: plus one WhatsApp or email code; high: plus both); a refresh in the middle loses nothing; every session records where it came from."""
from __future__ import annotations

import re
from email.message import EmailMessage

import pytest

from salesai.modules.auth import totp
from tests.admin_helpers import (
    A1,
    admin_login,
    email_code,
    forget_codes,
    make_admin,
    sign_in,
    step_up,
    whatsapp_code,
)
from tests.test_admin_auth import _settings
from tests.test_api import V1
from tests.test_founder_sign_in import another_browser, bearer, credentials, fresh_step, otp_rows


async def send(client, ticket: str, channel: str):
    return await client.post(f"{A1}/auth/step-up/send", json={"ticket": ticket, "channel": channel})


async def verify(client, ticket: str, code: str, channel: str):
    return await client.post(f"{A1}/auth/step-up/verify", json={"ticket": ticket, "code": code, "channel": channel})


async def a_trusted_founder(world, client):
    """A founder who has signed in once from this browser, so the next sign-in here is routine."""
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret)
    await fresh_step(world)
    return phone, secret


async def last_session(world) -> dict:
    async with world.rt.db.console_tx() as c:
        return dict(await (await c.execute("SELECT * FROM admin_sessions WHERE stage='session' ORDER BY created_at DESC LIMIT 1")).fetchone())


# ---------------------------------------------------------------- what each level asks for

async def test_a_routine_sign_in_needs_only_the_authenticator(world, client):
    await a_trusted_founder(world, client)
    mails = await _sim_codes(world)
    r = (await sign_in(client, world.rt, (await _secret(world)))).json()
    assert "access_token" in r and "step_up" not in r
    s = await last_session(world)
    assert s["risk"] == "routine" and s["method"] == "authenticator" and await _sim_codes(world) == mails        # nothing was sent anywhere


async def test_a_new_browser_is_elevated_and_either_channel_is_enough(world, client):
    phone, secret = await a_trusted_founder(world, client)
    async with another_browser(client) as other:
        r = (await sign_in(other, world.rt, secret, steps_ahead=1)).json()
        assert (r["step_up"], r["risk"], r["reason"], r["needs"]) == ("codes", "elevated", "new_device", 1) and r["channels"] == ["whatsapp", "email"] and r["done"] == []
        assert (await send(other, r["ticket"], "email")).status_code == 202
        done = await verify(other, r["ticket"], await email_code(other, phone), "email")
        assert done.status_code == 200 and "access_token" in done.json()
    s = await last_session(world)
    assert s["method"] == "authenticator+email" and s["risk"] == "elevated" and s["step_up_reason"] == "new_device"
    await fresh_step(world)
    async with another_browser(client) as third:
        r = (await sign_in(third, world.rt, secret, steps_ahead=1)).json()
        assert "access_token" in (await step_up(third, phone, r["ticket"], channels=("whatsapp",))).json()
    assert (await last_session(world))["method"] == "authenticator+whatsapp"


async def test_the_first_sign_in_is_high_and_needs_both_channels(world, client):
    phone, secret = await make_admin(world)
    r = (await sign_in(client, world.rt, secret)).json()
    assert (r["step_up"], r["risk"], r["reason"], r["needs"]) == ("codes", "high", "first_sign_in", 2)
    t = r["ticket"]
    assert (await send(client, t, "whatsapp")).status_code == 202
    half = await verify(client, t, await whatsapp_code(client, phone), "whatsapp")
    assert half.status_code == 200 and "access_token" not in half.json()                    # one channel is not enough
    body = half.json()
    assert (body["needs"], body["channels"], body["done"], body["ticket"]) == (1, ["email"], ["whatsapp"], t)
    assert (await send(client, t, "email")).status_code == 202
    done = await verify(client, t, await email_code(client, phone), "email")
    assert "access_token" in done.json()
    s = await last_session(world)
    assert s["method"] == "authenticator+whatsapp+email" and s["proofs"] == s["method"] and s["risk"] == "high"


async def test_wrong_codes_before_a_login_make_it_high_even_on_a_trusted_browser(world, client):
    phone, secret = await a_trusted_founder(world, client)
    assert (await client.post(f"{A1}/auth/sign-in", json={"code": "000000"})).status_code == 401
    r = (await sign_in(client, world.rt, secret, steps_ahead=1)).json()
    assert (r["risk"], r["reason"], r["needs"]) == ("high", "wrong_codes", 2)


async def test_a_new_network_alone_is_elevated_but_a_new_browser_on_a_new_network_is_high(world, client):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret, headers={"X-Forwarded-For": "203.0.113.10"})
    await fresh_step(world)
    far = (await sign_in(client, world.rt, secret, headers={"X-Forwarded-For": "198.51.100.7"})).json()
    assert (far["risk"], far["reason"], far["needs"]) == ("elevated", "new_network", 1)
    await fresh_step(world)
    async with another_browser(client) as other:
        both = (await sign_in(other, world.rt, secret, headers={"X-Forwarded-For": "192.0.2.44"})).json()
        assert (both["risk"], both["needs"]) == ("high", 2)


# ---------------------------------------------------------------- a refresh in the middle loses nothing; nothing is sent twice

async def test_the_progress_survives_a_refresh_and_asking_for_it_sends_nothing(world, client):
    phone, secret = await make_admin(world)
    t = (await sign_in(client, world.rt, secret)).json()["ticket"]
    assert (await send(client, t, "whatsapp")).status_code == 202
    assert (await verify(client, t, await whatsapp_code(client, phone), "whatsapp")).status_code == 200
    before = (await otp_rows(world), await _sim_codes(world))
    again = await client.post(f"{A1}/auth/step-up/status", json={"ticket": t})              # what the page asks for after a refresh
    assert again.status_code == 200
    j = again.json()
    assert (j["step_up"], j["risk"], j["needs"], j["channels"], j["done"]) == ("codes", "high", 1, ["email"], ["whatsapp"]) and 0 < j["expires_in"] <= 300
    assert (await otp_rows(world), await _sim_codes(world)) == before                       # a refresh never sends a code
    assert (await client.post(f"{A1}/auth/step-up/status", json={"ticket": "x" * 40})).status_code == 401


async def test_a_channel_cannot_be_used_twice_and_a_resend_waits_a_moment(world, client):
    phone, secret = await make_admin(world)
    t = (await sign_in(client, world.rt, secret)).json()["ticket"]
    assert (await send(client, t, "whatsapp")).json()["resend_in"] == 30
    assert (await send(client, t, "whatsapp")).status_code == 429                          # too soon
    assert (await verify(client, t, await whatsapp_code(client, phone), "whatsapp")).status_code == 200
    assert (await send(client, t, "whatsapp")).status_code == 409                          # that proof is already given
    assert (await client.post(f"{A1}/auth/step-up/send", json={"ticket": t, "channel": "sms"})).status_code == 422


# ---------------------------------------------------------------- the emailed code

async def test_the_email_code_is_eight_digits_hashed_single_use_and_limited(world, client):
    phone, secret = await make_admin(world)
    t = (await sign_in(client, world.rt, secret)).json()["ticket"]
    assert (await send(client, t, "email")).status_code == 202
    code = await email_code(client, phone)
    assert re.fullmatch(r"[0-9]{8}", code)
    async with world.rt.db.console_tx() as c:
        rows = await (await c.execute("SELECT * FROM admin_email_codes")).fetchall()
    assert len(rows) == 1 and code not in str(rows[0]) and re.fullmatch(r"[0-9a-f]{64}", rows[0]["code_hash"])
    for _ in range(4):
        assert (await verify(client, t, "00000000", "email")).status_code == 401
    ok = await verify(client, t, code, "email")                                           # the fifth try, and the right code
    assert ok.status_code == 200 and ok.json()["done"] == ["email"]
    assert (await verify(client, t, code, "email")).status_code == 409                    # a proof already given; and a used code never works again


async def test_five_wrong_email_codes_refuse_even_the_right_one(world, client):
    phone, secret = await make_admin(world)
    t = (await sign_in(client, world.rt, secret)).json()["ticket"]
    await send(client, t, "email")
    code = await email_code(client, phone)
    for _ in range(5):
        assert (await verify(client, t, "00000000", "email")).status_code in (401, 429)
    assert (await verify(client, t, code, "email")).status_code in (401, 429)


async def test_an_email_code_expires_after_ten_minutes(world, client):
    phone, secret = await make_admin(world)
    t = (await sign_in(client, world.rt, secret)).json()["ticket"]
    await send(client, t, "email")
    code = await email_code(client, phone)
    async with world.rt.db.console_tx() as c:
        await c.execute("UPDATE admin_email_codes SET expires_at = now() - interval '1 second'")
    assert (await verify(client, t, code, "email")).status_code == 401


async def test_no_email_is_sent_without_a_ticket_and_a_wrong_authenticator_code_sends_nothing(world, client):
    await make_admin(world)
    before = await _sim_codes(world)
    for _ in range(3):
        assert (await client.post(f"{A1}/auth/sign-in", json={"code": "000000"})).status_code == 401
    assert (await send(client, "y" * 40, "email")).status_code == 401
    async with world.rt.db.console_tx() as c:
        assert (await (await c.execute("SELECT count(*) AS n FROM admin_email_codes")).fetchone())["n"] == 0
    assert await _sim_codes(world) == before


async def test_a_real_email_comes_from_the_access_address_and_says_what_it_is(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    sent: list[EmailMessage] = []
    monkeypatch.setattr(world.rt.settings, "otp_channel", "whatsapp_cloud")                  # not the simulator: the real path
    monkeypatch.setattr(world.rt.admin_auth, "deliver", lambda m: sent.append(m))
    t = (await sign_in(client, world.rt, secret)).json()["ticket"]
    assert (await send(client, t, "email")).status_code == 202
    (m,) = sent
    assert m["From"] == "ChatDeal Access <access@chatdeal.example>" and m["To"] == "founder@chatdeal.example"
    assert m["Subject"] == "Your ChatDeal sign-in code" and m["Reply-To"] is None and m["Bcc"] is None
    code = re.search(r"([0-9]{8})", m.get_body(("plain",)).get_content()).group(1)
    assert code in m.get_body(("html",)).get_content() and "expires in 10 minutes" in m.get_body(("plain",)).get_content()
    assert (await verify(client, t, code, "email")).status_code == 200


async def test_a_failed_email_never_lets_the_sign_in_through_and_leaves_no_code_behind(world, client, monkeypatch):
    phone, secret = await make_admin(world)

    def boom(_m):
        raise RuntimeError("MessageRejected")
    monkeypatch.setattr(world.rt.settings, "otp_channel", "whatsapp_cloud")
    monkeypatch.setattr(world.rt.admin_auth, "deliver", boom)
    t = (await sign_in(client, world.rt, secret)).json()["ticket"]
    r = await send(client, t, "email")
    assert r.status_code == 502 and "Try WhatsApp" in r.text
    async with world.rt.db.console_tx() as c:
        assert (await (await c.execute("SELECT count(*) AS n FROM admin_email_codes")).fetchone())["n"] == 0
    assert (await verify(client, t, "12345678", "email")).status_code == 401
    assert (await send(client, t, "whatsapp")).status_code == 202                            # the other channel still works


# ---------------------------------------------------------------- the founder is three settings, and the database holds none of them

async def test_changing_the_email_or_the_number_or_the_secret_ends_every_session(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    for name, value in (("founder_email", "someone.else@chatdeal.example"), ("founder_totp_secret", totp.new_secret())):
        tokens = await admin_login(client, world.rt, phone, secret)
        assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 200
        monkeypatch.setattr(world.rt.settings, name, value)
        assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 401
        monkeypatch.undo()
        await fresh_step(world)


async def test_a_wiped_database_needs_nothing_re_done(world, client):
    phone, secret = await make_admin(world)
    async with world.rt.db.console_tx() as c:                                                # the database is lost: no account, no state, no sessions
        await c.execute("DELETE FROM admin_sessions")
        await c.execute("DELETE FROM admin_credentials")
        await c.execute("DELETE FROM accounts WHERE phone=%s", (phone,))
    await forget_codes(world)
    r = (await sign_in(client, world.rt, secret)).json()
    assert (r["risk"], r["reason"], r["needs"]) == ("high", "first_sign_in", 2)               # the same settings work; it is simply a first sign-in again
    assert "access_token" in (await step_up(client, phone, r["ticket"])).json()
    assert (await credentials(world))["totp_secret_enc"] is None


def test_the_three_founder_settings_are_required_and_checked_for_the_console_only():
    assert _settings().founder_email == "founder@chatdeal.example"
    for missing in ("founder_email", "founder_totp_secret", "admin_email_from"):
        with pytest.raises(ValueError, match=missing.upper()):
            _settings(**{missing: ""})
    with pytest.raises(ValueError, match="FOUNDER_EMAIL"):
        _settings(founder_email="not an email")
    with pytest.raises(ValueError, match="FOUNDER_TOTP_SECRET"):
        _settings(founder_totp_secret="too short")
    assert _settings(founder_email=" Founder@ChatDeal.Example ").founder_email == "founder@chatdeal.example"
    assert _settings(founder_totp_secret="jbsw y3dp ehpk 3pxp jbsw").founder_totp_secret == "JBSWY3DPEHPK3PXPJBSW"          # written as authenticator apps show it
    s = _settings(role="api", founder_email="", founder_totp_secret="", admin_email_from="", master_key="", admin_jwt_secret="", simulator_enabled=False)       # the public part never needs them
    assert s.founder_totp_secret == ""


def test_the_console_needs_a_way_to_send_its_code_emails_outside_the_simulator():
    with pytest.raises(ValueError, match="SES_REGION"):
        _settings(ses_region="", otp_channel="whatsapp_cloud", platform_wa_access_token="x")
    assert _settings(ses_region="", otp_channel="simulator").role == "admin"
    assert _settings(email_transport="smtp", smtp_host="smtp.example", ses_region="", otp_channel="whatsapp_cloud", platform_wa_access_token="x").email_transport == "smtp"


def test_the_new_secret_command_prints_a_usable_secret_and_stores_nothing(capsys):
    from salesai.__main__ import admin_new_secret
    admin_new_secret()
    out = capsys.readouterr().out
    secret = re.search(r"FOUNDER_TOTP_SECRET=([A-Z2-7]{32})", out).group(1)
    assert "stored nowhere" in out and "otpauth://totp/" in out and "password manager" in out
    assert _settings(founder_totp_secret=secret).founder_totp_secret == secret


# ---------------------------------------------------------------- every session is recorded with everything the console can learn

async def test_a_session_records_where_what_and_how_it_was_proven(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    s = world.rt.settings
    monkeypatch.setattr(s, "admin_geo_header", "CF-IPCountry")
    monkeypatch.setattr(s, "admin_geo_city_header", "CF-IPCity")
    monkeypatch.setattr(s, "admin_geo_region_header", "CF-Region")
    h = {"X-Forwarded-For": "203.0.113.9", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/142.0 Safari/537.36", "Accept-Language": "en-IN,en;q=0.8",
         "CF-IPCountry": "IN", "CF-IPCity": "Pune", "CF-Region": "Maharashtra"}
    tokens = await admin_login(client, world.rt, phone, secret, headers=h)
    rows = (await client.get(f"{A1}/auth/sessions", headers=bearer(tokens))).json()
    r = rows[0]
    assert r["ip"] == "203.0.113.9" and r["country"] == "IN" and r["city"] == "Pune" and r["region"] == "Maharashtra" and r["language"] == "en-IN"
    assert r["device"].startswith("Chrome 142 on Windows") and r["risk"] == "high" and r["proofs"] == "authenticator+whatsapp+email" == r["method"]
    assert r["step_up_reason"] == "first_sign_in" and r["network"] == "203.0.113.0/24" and r["status"] == "active" and r["is_current"] is True


async def test_place_headers_are_untrusted_text_and_are_cleaned(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    s = world.rt.settings
    monkeypatch.setattr(s, "admin_geo_city_header", "CF-IPCity")
    h = {"CF-IPCity": "<script>alert(1)</script> " + "x" * 200, "Accept-Language": "<b>bad</b>"}
    tokens = await admin_login(client, world.rt, phone, secret, headers=h)
    r = (await client.get(f"{A1}/auth/sessions", headers=bearer(tokens))).json()[0]
    assert "<" not in (r["city"] or "") and len(r["city"] or "") <= 60 and r["language"] is None


async def test_a_risky_sign_in_tells_the_founder_and_a_routine_one_does_not(world, client):
    phone, secret = await a_trusted_founder(world, client)
    async with world.rt.db.system_tx() as c:
        await c.execute("DELETE FROM operator_alerts WHERE kind='admin_new_sign_in'")
    assert "access_token" in (await sign_in(client, world.rt, secret, steps_ahead=1)).json()
    async with world.rt.db.system_tx() as c:
        assert (await (await c.execute("SELECT count(*) AS n FROM operator_alerts WHERE kind='admin_new_sign_in'")).fetchone())["n"] == 0
    await fresh_step(world)
    async with another_browser(client) as other:
        r = (await sign_in(other, world.rt, secret, steps_ahead=1)).json()
        await step_up(other, phone, r["ticket"], channels=("email",))
    async with world.rt.db.system_tx() as c:
        a = await (await c.execute("SELECT severity, message FROM operator_alerts WHERE kind='admin_new_sign_in'")).fetchone()
    assert a["severity"] == "high" and "elevated" in a["message"] and "authenticator + email" in a["message"]


# ---------------------------------------------------------------- helpers

async def _sim_codes(world) -> int:
    async with world.rt.db.system_tx() as c:
        return int((await (await c.execute("SELECT count(*) AS n FROM sim_messages WHERE template_name IN ('admin_login_code_email','otp_login')")).fetchone())["n"])


async def _secret(world) -> str:
    return str(world.rt.settings.founder_totp_secret)
