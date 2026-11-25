"""ADR 0047: one founder, fixed by the deployment; the authenticator is the first proof; a WhatsApp code is the second layer only when a sign-in looks
suspicious; enrolment is a command on the server that prints a QR code."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from salesai.modules.auth.admin import MAX_DEVICES, enroll_admin, network_of
from salesai.qr import printable_qr, qr_ascii, qr_text
from tests.admin_helpers import (
    A1,
    admin_login,
    code_for,
    forget_codes,
    make_admin,
    sign_in,
    step_up,
    whatsapp_code,
)
from tests.test_admin_auth import _settings, audit
from tests.test_api import V1

SRC = Path(__file__).resolve().parents[1] / "src" / "salesai"


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def fresh_step(world) -> None:
    """Make the current authenticator code usable again, as it is once the next 30-second step comes round (a used code works only once)."""
    async with world.rt.db.console_tx() as c:
        await c.execute("UPDATE admin_credentials SET last_used_step = 0")


def another_browser(client) -> httpx.AsyncClient:
    """A second browser: same server, its own cookies (so no trusted-browser cookie), the same address headers."""
    return httpx.AsyncClient(transport=client._transport, base_url=str(client.base_url), headers=dict(client.headers))   # noqa: SLF001


async def otp_rows(world) -> int:
    async with world.rt.db.system_tx() as c:
        return (await (await c.execute("SELECT count(*) AS n FROM otp_deliveries")).fetchone())["n"]


async def credentials(world) -> dict:
    async with world.rt.db.console_tx() as c:
        return dict(await (await c.execute("SELECT * FROM admin_credentials WHERE account_id=(SELECT id FROM accounts WHERE phone=%s)", (world.rt.settings.founder_phone,))).fetchone())


# ---------------------------------------------------------------- one founder, fixed by the deployment

async def test_only_the_number_in_the_deployment_setting_can_ever_sign_in(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 200
    monkeypatch.setattr(world.rt.settings, "founder_phone", "+919000011111")              # the deployment names somebody else
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 401      # the old session ends at once
    r = (await sign_in(client, world.rt, secret, steps_ahead=1)).json()                    # the deployment's own settings decide who the founder is: now that number
    assert r["step_up"] == "codes"
    done = (await step_up(client, "+919000011111", r["ticket"])).json()
    assert (await client.get(f"{A1}/auth/me", headers=bearer(done))).json()["phone"] == "+919000011111"
    async with world.rt.db.console_tx() as c:
        ops = {x["phone"] for x in await (await c.execute("SELECT phone FROM accounts WHERE platform_role='operator'")).fetchall()}
    assert ops == {"+919000011111"}                                                          # and nobody else is an operator


async def test_editing_the_founders_number_in_the_database_locks_them_out_and_lets_nobody_in(world, client):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    other = f"+9192{uuid.uuid4().int % 10**8:08d}"
    async with world.rt.db.system_tx() as c:                          # an attacker with database access renames the account to a number they hold
        await c.execute("UPDATE accounts SET phone=%s WHERE phone=%s", (other, phone))
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 401      # the session ends: the number is not the deployment's
    async def sessions_of_renamed() -> int:
        async with world.rt.db.console_tx() as c:
            return int((await (await c.execute("SELECT count(*) AS n FROM admin_sessions s JOIN accounts a ON a.id=s.account_id WHERE a.phone=%s", (other,))).fetchone())["n"])
    before = await sessions_of_renamed()
    r = (await sign_in(client, world.rt, secret, steps_ahead=1)).json()                                    # the founder (who holds the secret) gets back in, on the row the deployment names
    assert r["step_up"] == "codes"
    async with world.rt.db.console_tx() as c:
        renamed = await (await c.execute("SELECT platform_role FROM accounts WHERE phone=%s", (other,))).fetchone()
    assert renamed["platform_role"] is None and await sessions_of_renamed() == before                     # the renamed account is nobody: no role, and no new session
    async with world.rt.db.console_tx() as c:
        await c.execute("DELETE FROM admin_credentials WHERE account_id IN (SELECT id FROM accounts WHERE phone=%s)", (phone,))     # the row the deployment's settings recreated
        await c.execute("DELETE FROM accounts WHERE phone=%s", (phone,))
        await c.execute("UPDATE accounts SET phone=%s WHERE phone=%s", (phone, other))


async def test_another_operator_account_is_not_a_founder_even_with_an_authenticator_of_its_own(world, client):
    phone, secret = await make_admin(world)
    other = f"+9192{uuid.uuid4().int % 10**8:08d}"
    async with world.rt.db.console_tx() as c:
        acct = await (await c.execute("INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Second','operator') RETURNING id", (other,))).fetchone()
        await c.execute("INSERT INTO admin_credentials (account_id) VALUES (%s)", (acct["id"],))        # state for it too: the only secret there is, is the deployment's
    r = await sign_in(client, world.rt, secret)
    assert r.status_code == 200 and r.json().get("step_up") == "codes"           # the founder gets in (with the second layer, fresh browser)
    t = r.json()["ticket"]
    async with world.rt.db.console_tx() as c:
        sub = (await (await c.execute("SELECT account_id FROM admin_sessions ORDER BY created_at DESC LIMIT 1")).fetchone())["account_id"]
    assert str(sub) != str(acct["id"])                                                    # never the second account
    assert (await step_up(client, phone, t)).status_code == 200


async def test_enrolment_needs_the_deployment_setting_and_makes_the_founder_the_only_operator(world, monkeypatch):
    other = f"+9192{uuid.uuid4().int % 10**8:08d}"
    async with world.rt.db.system_tx() as c:
        await c.execute("INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Old operator','operator')", (other,))
    monkeypatch.setattr(world.rt.settings, "founder_phone", "")
    with pytest.raises(ValueError, match="FOUNDER_PHONE"):
        await enroll_admin(world.rt.db, world.rt.settings)
    monkeypatch.undo()
    await make_admin(world)
    async with world.rt.db.console_tx() as c:
        ops = {r["phone"] for r in await (await c.execute("SELECT phone FROM accounts WHERE platform_role='operator'")).fetchall()}
        creds = (await (await c.execute("SELECT count(*) AS n FROM admin_credentials")).fetchone())["n"]
    assert ops == {world.rt.settings.founder_phone} and creds == 1


async def test_no_code_anywhere_changes_an_accounts_phone_number():
    """The founder's number cannot be taken over through the product: nothing writes accounts.phone after the account exists."""
    pattern = re.compile(r"UPDATE\s+accounts\s+SET\s+(?:(?!WHERE)[^\"';])*?\bphone\s*=", re.I | re.S)       # the SET list of one statement, never past its WHERE or its string
    offenders = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_the_console_part_cannot_start_without_the_founders_number():
    assert _settings().founder_phone == "+919999900000"
    with pytest.raises(ValueError, match="FOUNDER_PHONE"):
        _settings(founder_phone="")
    with pytest.raises(ValueError, match="FOUNDER_PHONE"):
        _settings(founder_phone="not a number")
    assert _settings(founder_phone="98765 43210").founder_phone == "+919876543210"           # written the way people write it, kept the way the database keeps it


def test_the_public_part_does_not_need_or_use_the_founders_number():
    s = _settings(role="api", founder_phone="", master_key="", admin_jwt_secret="", simulator_enabled=False)         # starts fine without it
    assert s.founder_phone == ""


# ---------------------------------------------------------------- the authenticator first, nothing sent to WhatsApp unless it passed

async def test_a_wrong_authenticator_code_never_causes_a_whatsapp_message(world, client):
    await make_admin(world)
    before = await otp_rows(world)
    for _ in range(3):
        assert (await client.post(f"{A1}/auth/sign-in", json={"code": "000000"})).status_code == 401
    assert await otp_rows(world) == before


async def test_the_whatsapp_code_is_only_sent_to_someone_holding_a_ticket(world, client):
    phone, secret = await make_admin(world)
    before = await otp_rows(world)
    for bad in ("not-a-ticket-" + "x" * 30, "a" * 60):
        assert (await client.post(f"{A1}/auth/step-up/send", json={"ticket": bad, "channel": "whatsapp"})).status_code == 401
        assert (await client.post(f"{A1}/auth/step-up/send", json={"ticket": bad, "channel": "email"})).status_code == 401
    tokens = await admin_login(client, world.rt, phone, secret)                              # a full session is not a ticket either
    assert (await client.post(f"{A1}/auth/step-up/send", json={"ticket": tokens["access_token"], "channel": "whatsapp"})).status_code == 401
    assert await otp_rows(world) == before + 1                                               # only the legitimate step-up sent one code


async def test_a_wrong_whatsapp_code_is_refused_and_counts_toward_the_same_lock(world, client):
    phone, secret = await make_admin(world)
    t = (await sign_in(client, world.rt, secret)).json()["ticket"]
    assert (await client.post(f"{A1}/auth/step-up/send", json={"ticket": t, "channel": "whatsapp"})).status_code == 202
    for _ in range(5):
        assert (await client.post(f"{A1}/auth/step-up/verify", json={"ticket": t, "code": "000000", "channel": "whatsapp"})).status_code == 401
    r = await client.post(f"{A1}/auth/step-up/verify", json={"ticket": t, "code": await whatsapp_code(client, phone), "channel": "whatsapp"})
    assert r.status_code == 429                                                                 # even the right code, while locked
    assert (await sign_in(client, world.rt, secret, steps_ahead=1)).status_code == 429


# ---------------------------------------------------------------- when the second layer applies

async def test_the_first_sign_in_asks_for_the_whatsapp_code_and_then_the_browser_is_trusted(world, client):
    phone, secret = await make_admin(world)
    first = (await sign_in(client, world.rt, secret)).json()
    assert first["step_up"] == "codes" and first["reason"] == "first_sign_in" and "access_token" not in first
    done = await step_up(client, phone, first["ticket"])
    assert done.status_code == 200 and "access_token" in done.json()
    again = (await sign_in(client, world.rt, secret, steps_ahead=1)).json()                     # same browser, same place, nothing odd
    assert "access_token" in again and "step_up" not in again


async def test_a_browser_the_founder_has_not_signed_in_from_needs_the_whatsapp_code(world, client):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret)
    async with another_browser(client) as other:
        r = (await sign_in(other, world.rt, secret, steps_ahead=1)).json()
        assert r["step_up"] == "codes" and r["reason"] == "new_device"


async def test_wrong_codes_since_the_last_good_sign_in_make_the_next_one_ask_for_whatsapp_even_on_a_trusted_browser(world, client):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret)
    await fresh_step(world)
    assert (await client.post(f"{A1}/auth/sign-in", json={"code": "000000"})).status_code == 401       # someone guessed
    r = (await sign_in(client, world.rt, secret)).json()
    assert r["step_up"] == "codes" and r["reason"] == "wrong_codes"
    assert (await step_up(client, phone, r["ticket"])).status_code == 200
    await fresh_step(world)
    assert "access_token" in (await sign_in(client, world.rt, secret)).json()                          # proven again: back to routine


async def test_a_new_network_asks_for_the_whatsapp_code_but_the_same_neighbourhood_does_not(world, client):
    phone, secret = await make_admin(world)
    home = {"X-Forwarded-For": "203.0.113.10"}
    await admin_login(client, world.rt, phone, secret, headers=home)
    await fresh_step(world)
    near = (await sign_in(client, world.rt, secret, headers={"X-Forwarded-For": "203.0.113.200"})).json()
    assert "access_token" in near                                                                    # same /24
    await fresh_step(world)
    far = (await sign_in(client, world.rt, secret, headers={"X-Forwarded-For": "198.51.100.7"})).json()
    assert far["step_up"] == "codes" and far["reason"] == "new_network"


def test_networks_are_compared_by_neighbourhood_not_exact_address():
    assert network_of("203.0.113.10") == network_of("203.0.113.250") != network_of("203.0.114.10")
    assert network_of("2001:db8:1:2::1") == network_of("2001:db8:1:ffff::9") != network_of("2001:db8:2::1")
    assert network_of(None) is None and network_of("not an address") is None


async def test_a_lock_makes_the_next_good_sign_in_ask_for_whatsapp(world, client):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret)
    for _ in range(5):
        await client.post(f"{A1}/auth/sign-in", json={"code": "000000"})
    async with world.rt.db.console_tx() as c:
        await c.execute("UPDATE admin_credentials SET locked_until = now() - interval '1 second'")
    r = (await sign_in(client, world.rt, secret, steps_ahead=1)).json()
    assert r["step_up"] == "codes"
    assert (await step_up(client, phone, r["ticket"])).status_code == 200
    assert (await credentials(world))["was_locked"] is False                                         # a full sign-in clears it


async def test_a_trusted_browser_is_forgotten_after_the_trust_period(world, client, monkeypatch):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret)
    later = datetime.now(UTC).timestamp() + (world.rt.settings.admin_device_trust_days + 1) * 86400
    monkeypatch.setattr(world.rt.admin_auth, "clock", lambda: later)
    r = (await sign_in(client, world.rt, secret)).json()
    assert r["step_up"] == "codes" and r["reason"] == "new_device"


async def test_at_most_five_browsers_stay_trusted_and_the_oldest_is_dropped(world, client):
    phone, secret = await make_admin(world)
    browsers = []
    for _ in range(MAX_DEVICES + 1):
        b = another_browser(client)
        browsers.append(b)
        await fresh_step(world)
        await forget_codes(world)                                           # six step-ups in an hour would (rightly) trip the per-number limit
        await admin_login(b, world.rt, phone, secret)
    assert len((await credentials(world))["trusted_devices"]) == MAX_DEVICES
    await fresh_step(world)
    oldest = (await sign_in(browsers[0], world.rt, secret)).json()
    await fresh_step(world)
    newest = (await sign_in(browsers[-1], world.rt, secret)).json()
    assert oldest.get("reason") == "new_device" and "access_token" in newest
    for b in browsers:
        await b.aclose()


async def test_enrolling_again_forgets_every_trusted_browser_and_signs_everyone_out(world, client):
    phone, secret = await make_admin(world)
    tokens = await admin_login(client, world.rt, phone, secret)
    again = await enroll_admin(world.rt.db, world.rt.settings)
    assert again == secret
    assert (await client.get(f"{V1}/operator/businesses", headers=bearer(tokens))).status_code == 401
    assert (await credentials(world))["trusted_devices"] == []
    r = (await sign_in(client, world.rt, secret, steps_ahead=1)).json()
    assert r["step_up"] == "codes" and r["reason"] == "first_sign_in" and r["risk"] == "high"


# ---------------------------------------------------------------- the trusted-browser cookie

async def test_the_trusted_browser_cookie_is_locked_down_and_only_its_hash_is_stored(world, client):
    phone, secret = await make_admin(world)
    first = (await sign_in(client, world.rt, secret)).json()
    done = await step_up(client, phone, first["ticket"])
    cookie = done.headers["set-cookie"]
    assert "admin_device=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/admin/v1/auth" in cookie
    token = re.search(r"admin_device=([^;]+)", cookie).group(1)
    assert token not in done.text                                                              # never in the body, where scripts could read it
    stored = (await credentials(world))["trusted_devices"]
    assert len(stored) == 1 and token not in str(stored) and re.fullmatch(r"[0-9a-f]{64}", stored[0]["h"])


# ---------------------------------------------------------------- the founder is told when someone is guessing

async def test_three_wrong_codes_tell_the_founder_and_a_lock_tells_them_louder(world, client):
    await make_admin(world)
    for _ in range(3):
        await client.post(f"{A1}/auth/sign-in", json={"code": "000000"})
    async with world.rt.db.system_tx() as c:
        warn = await (await c.execute("SELECT severity FROM operator_alerts WHERE kind='admin_wrong_codes' AND resolved_at IS NULL")).fetchone()
    assert warn is not None and warn["severity"] == "high"
    for _ in range(2):
        await client.post(f"{A1}/auth/sign-in", json={"code": "000000"})
    async with world.rt.db.system_tx() as c:
        lock = await (await c.execute("SELECT severity FROM operator_alerts WHERE kind='admin_locked' AND resolved_at IS NULL")).fetchone()
    assert lock is not None and lock["severity"] == "critical" and await audit(world, "admin.locked")


async def test_a_step_up_is_audited_with_its_reason(world, client):
    phone, secret = await make_admin(world)
    await admin_login(client, world.rt, phone, secret)
    rows = await audit(world, "admin.step_up_required")
    assert rows and "first_sign_in" in str(rows[-1]["detail"])


# ---------------------------------------------------------------- the QR code at enrolment

URI = "otpauth://totp/ChatDeal%20Admin:%2B919999900000?secret=QUZHKPENHAVQYW63TGVY5ZYA3NY4TTUO&issuer=ChatDeal%20Admin&digits=6&period=30"


def test_the_enrolment_qr_is_a_real_qr_code_in_both_drawings():
    import segno
    modules = len(segno.make(URI, error="m").matrix)
    assert qr_text(URI).count("\n") >= modules // 2                       # half-blocks: two QR rows per line
    ascii_drawing = qr_ascii(URI)
    assert "##" in ascii_drawing and ascii_drawing.count("\n") >= modules
    assert printable_qr(URI, "utf-8") == qr_text(URI)
    assert printable_qr(URI, "cp1252") == ascii_drawing                    # a console that cannot print blocks gets the plain drawing


async def test_the_enrol_command_prints_the_qr_the_secret_and_the_link_and_the_secret_works(world, client, capsys):
    from salesai.__main__ import admin_enroll
    await admin_enroll()
    out = capsys.readouterr().out
    assert "The authenticator for the founder (+919999900000, founder@chatdeal.example)" in out and "otpauth://totp/" in out
    secret = re.search(r"secret=([A-Z2-7]+)", out).group(1)
    assert secret == world.rt.settings.founder_totp_secret                  # the QR is the deployment's own secret: nothing new is made or stored
    assert secret in out                                                    # the typed fallback
    assert any(ch in out for ch in "█▀▄#")                                  # the QR drawing
    r = await client.post(f"{A1}/auth/sign-in", json={"code": code_for(world.rt, secret)})
    assert r.status_code == 200 and r.json()["step_up"] == "codes" and r.json()["risk"] == "high"  # the first sign-in after enrolment: both extra codes
