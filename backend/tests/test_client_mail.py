"""ADR 0054: mail to shop owners. Payment confirmations and support notices go out from the right sender, never hold anything up, never repeat, never
carry a ticket's text, and every template renders safely. Transport is a recording fake."""
from __future__ import annotations

import re
from email.message import EmailMessage

import pytest

from salesai.config import Settings
from salesai.modules.billing import TestGateway
from salesai.modules.mail import CATALOG, MailDispatcher, enqueue, render
from salesai.modules.mail.templates import inr, purpose_of
from tests.test_api import V1, hdr, operator_token
from tests.test_billing import new_shop

SITE = "https://app.chatdeal.example"


class Sent:
    def __init__(self, fail: Exception | None = None):
        self.msgs: list[EmailMessage] = []
        self.fail = fail

    def __call__(self, msg: EmailMessage) -> str | None:
        if self.fail:
            raise self.fail
        self.msgs.append(msg)
        return f"ref-{len(self.msgs)}"


@pytest.fixture(autouse=True)
async def clean_outbox(world):
    """The database is shared across the suite: start each test with no other mail waiting, so a round only sees this test's rows."""
    async with world.rt.db.system_tx() as c:
        await c.execute("DELETE FROM mail_outbox")
    world.rt.billing.provider = TestGateway()          # an instant gateway: paying settles at once, whatever an earlier test left


def dispatcher(world, sent: Sent, **kw) -> MailDispatcher:
    return MailDispatcher(world.rt.db, deliver=sent, sender_for=lambda p: f"{p}@chatdeal.example",
                          bcc_for=lambda p: ["copy@chatdeal.example"] if p == "support" else [], site_url=SITE, **kw)


async def shop_with_email(world, client, email="owner@shop.example"):
    owner = await new_shop(world, client, "Sharma <b>Mobiles</b>")
    if email:
        r = await client.patch(f"{V1}/business", headers=hdr(owner), json={"billing_email": email})
        assert r.status_code == 200, r.text
    bid = (await client.get(f"{V1}/business", headers=hdr(owner))).json()["id"]
    async with world.rt.db.system_tx() as c:
        await c.execute("DELETE FROM mail_outbox WHERE business_id=%s", (bid,))
    return owner, bid


async def outbox(world, bid):
    async with world.rt.db.system_tx() as c:
        return await (await c.execute("SELECT * FROM mail_outbox WHERE business_id=%s ORDER BY id", (bid,))).fetchall()


async def pay_an_invoice(client, owner):
    inv = (await client.post(f"{V1}/billing/subscribe", headers=hdr(owner), json={"plan": "growth", "interval": "month"})).json()["invoice"]
    r = await client.post(f"{V1}/billing/invoices/{inv['id']}/pay", headers=hdr(owner))
    assert r.status_code == 200, r.text
    return inv


# ---------------------------------------------------------------- the templates
def test_every_template_renders_with_its_sample_and_leaves_nothing_unfilled():
    for t in CATALOG.values():
        m = render(t.name, t.sample, SITE)
        assert m.subject and "\n" not in m.subject, t.name
        assert "{" not in m.text and "{" not in m.subject and "None" not in m.text, t.name       # no placeholder, no "None"
        assert m.html.startswith("<!doctype html>") and 'lang="en"' in m.html, t.name
        assert t.purpose in ("billing", "support", "account", "updates", "access") and purpose_of(t.name) == t.purpose


def test_outside_text_cannot_add_markup_or_a_header():
    evil = {**CATALOG["ticket_received"].sample, "shop": "<script>alert(1)</script>", "subject": "Hi\nBcc: attacker@evil.example <img src=x onerror=1>"}
    m = render("ticket_received", evil, SITE)
    assert "<script>" not in m.html and "<img" not in m.html and "&lt;script&gt;" in m.html
    assert "\n" not in m.subject


def test_a_missing_field_fails_loudly_instead_of_sending_half_a_mail():
    with pytest.raises(KeyError):
        render("payment_confirmed", {"shop": "S"}, SITE)
    with pytest.raises(KeyError):
        render("no_such_template", {}, SITE)


def test_money_uses_indian_grouping():
    assert inr(117_800_00) == "₹1,17,800.00" and inr(99) == "₹0.99" and inr(12_34_567_89) == "₹12,34,567.89"


def test_a_ticket_mail_never_carries_the_ticket_text_and_links_to_the_app():
    m = render("ticket_replied", {**CATALOG["ticket_replied"].sample}, SITE)
    assert f"{SITE}/settings?tab=support" in m.text and f"{SITE}/settings?tab=support" in m.html


# ---------------------------------------------------------------- payment confirmation
async def test_paying_an_invoice_queues_one_confirmation_and_it_is_sent_from_billing(world, client):
    owner, bid = await shop_with_email(world, client)
    inv = await pay_an_invoice(client, owner)
    rows = await outbox(world, bid)
    assert [r["template"] for r in rows] == ["payment_confirmed"] and rows[0]["dedupe_key"] == f"payment_confirmed:{inv['id']}" and rows[0]["status"] == "pending"
    sent = Sent()
    assert await dispatcher(world, sent).dispatch() >= 1
    (msg,) = [m for m in sent.msgs if m["To"] == "owner@shop.example"]
    assert msg["From"] == "billing@chatdeal.example" and msg["To"] == "owner@shop.example" and msg["Reply-To"] == "billing@chatdeal.example"
    assert inv["number"] in msg["Subject"] and msg.get_body(("html",)) is not None
    assert msg["Bcc"] is None                                          # billing has no copy configured here
    (row,) = await outbox(world, bid)
    assert row["status"] == "sent" and row["provider_ref"].startswith("ref-")
    assert "owner@shop.example" not in str(row)                                                     # the address is never stored in the outbox


async def test_a_repeated_event_sends_one_mail_only(world, client):
    owner, bid = await shop_with_email(world, client)
    async with world.rt.db.system_tx() as c:
        assert await enqueue(c, bid, "trial_ending", {"days_left": 3, "ends_on": "2026-11-27"}, f"trial_ending:{bid}:2026-11-27")
        assert not await enqueue(c, bid, "trial_ending", {"days_left": 3, "ends_on": "2026-11-27"}, f"trial_ending:{bid}:2026-11-27")
    assert len(await outbox(world, bid)) == 1


async def test_a_shop_without_a_billing_email_is_skipped_quietly(world, client):
    owner, bid = await shop_with_email(world, client, email="")
    await pay_an_invoice(client, owner)
    sent = Sent()
    await dispatcher(world, sent).dispatch()
    (row,) = await outbox(world, bid)
    assert row["status"] == "skipped" and "no billing email" in row["error"]
    assert sent.msgs == []


async def test_a_failing_provider_never_blocks_the_payment_and_is_retried_then_given_up(world, client):
    owner, bid = await shop_with_email(world, client)
    await pay_an_invoice(client, owner)                               # the payment itself succeeded although no mail has gone out
    assert [i["status"] for i in (await client.get(f"{V1}/billing/invoices", headers=hdr(owner))).json()] == ["paid"]
    bad = dispatcher(world, Sent(RuntimeError("MessageRejected")), max_attempts=2)
    await bad.dispatch()
    (row,) = await outbox(world, bid)
    assert row["status"] == "pending" and row["attempts"] == 1 and "MessageRejected" in row["error"]
    async with world.rt.db.system_tx() as c:
        await c.execute("UPDATE mail_outbox SET next_attempt_at = now() WHERE id=%s", (row["id"],))
    await bad.dispatch()
    (row,) = await outbox(world, bid)
    assert row["status"] == "failed" and row["attempts"] == 2
    good = Sent()
    await dispatcher(world, good).dispatch()
    assert good.msgs == []                                                                         # a given-up mail is not tried again


async def test_the_rate_budget_limits_each_round(world, client):
    owner, bid = await shop_with_email(world, client)
    async with world.rt.db.system_tx() as c:
        for i in range(5):
            await enqueue(c, bid, "trial_ending", {"days_left": 3, "ends_on": "2026-11-27"}, f"budget:{bid}:{i}")
    sent = Sent()
    d = dispatcher(world, sent, per_minute=4)                                 # a round is half a minute: two mails
    assert await d.dispatch() == 2 and await d.dispatch() == 2 and await d.dispatch() == 1


async def test_client_mail_that_is_not_set_up_is_skipped_not_sent(world, client):
    owner, bid = await shop_with_email(world, client)
    async with world.rt.db.system_tx() as c:
        await enqueue(c, bid, "trial_ending", {"days_left": 1, "ends_on": "2026-11-27"}, f"off:{bid}")
    sent = Sent()
    await dispatcher(world, sent, enabled=False).dispatch()
    assert sent.msgs == [] and (await outbox(world, bid))[0]["status"] == "skipped"


# ---------------------------------------------------------------- support
async def test_a_support_reply_mails_a_link_and_never_the_reply_text(world, client):
    owner, bid = await shop_with_email(world, client)
    t = await client.post(f"{V1}/support/tickets", headers=hdr(owner), json={"subject": "Number disconnected", "category": "whatsapp", "message": "my secret detail 123"})
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    op = await operator_token(world, client)
    r = await client.post(f"{V1}/operator/support/{tid}/messages", headers=hdr(op), json={"body": "PRIVATE ANSWER about your account 9876"})
    assert r.status_code == 201, r.text
    rows = await outbox(world, bid)
    assert sorted(x["template"] for x in rows) == ["ticket_received", "ticket_replied"]
    assert "PRIVATE ANSWER" not in str(rows) and "my secret detail" not in str(rows)             # the outbox holds no ticket text
    sent = Sent()
    await dispatcher(world, sent).dispatch()
    mine = [m for m in sent.msgs if m["To"] == "owner@shop.example"]
    assert len(mine) == 2 and all(m["From"] == "support@chatdeal.example" and m["Bcc"] == "copy@chatdeal.example" for m in mine)
    assert all("PRIVATE ANSWER" not in m.as_string() and "my secret detail" not in m.as_string() for m in mine)
    again = await client.post(f"{V1}/operator/support/{tid}/messages", headers=hdr(op), json={"body": "second answer"})
    assert again.status_code == 201
    assert len(await outbox(world, bid)) == 3                                                    # each reply is its own mail


# ---------------------------------------------------------------- privacy, settings
async def test_one_shop_cannot_read_the_mail_outbox(world, client):
    owner, bid = await shop_with_email(world, client)
    async with world.rt.db.tenant(bid) as c:
        with pytest.raises(Exception, match="permission denied"):
            await c.execute("SELECT 1 FROM mail_outbox")


async def test_the_billing_email_is_validated_and_can_be_cleared(world, client):
    owner, bid = await shop_with_email(world, client)
    bad = await client.patch(f"{V1}/business", headers=hdr(owner), json={"billing_email": "not an email"})
    assert bad.status_code == 422
    cleared = await client.patch(f"{V1}/business", headers=hdr(owner), json={"billing_email": ""})
    assert cleared.status_code == 200 and cleared.json()["billing_email"] is None


def _settings(**kw) -> Settings:
    base = dict(database_url="postgresql://x@h/d", system_database_url="postgresql://x@h/d", pricing_database_url="postgresql://x@h/d",
                console_database_url="postgresql://x@h/d", migration_database_url="postgresql://x@h/d", role="all", llm_provider="local",
                master_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", jwt_secret="j" * 32, otp_secret="o" * 32, meta_app_secret="m" * 12, meta_verify_token="v" * 12)
    return Settings(**{**base, **kw})


def test_senders_fall_back_to_the_default_and_client_mail_needs_a_transport():
    s = _settings(client_email_from="hello@chatdeal.example", client_email_from_billing="billing@chatdeal.example", ses_region="ap-south-1")
    assert s.client_mail_enabled and s.client_sender("billing") == "billing@chatdeal.example" and s.client_sender("support") == "hello@chatdeal.example"
    assert not _settings().client_mail_enabled
    with pytest.raises(ValueError, match="SES_REGION"):
        _settings(client_email_from="hello@chatdeal.example")
    with pytest.raises(ValueError, match="BCC"):
        _settings(client_email_from="hello@chatdeal.example", ses_region="r", client_email_bcc_support="nope")
    assert _settings(client_email_from="h@chatdeal.example", ses_region="r", client_email_bcc_support="a@x.example, b@x.example").client_bcc("support") == ["a@x.example", "b@x.example"]


def test_the_owner_link_uses_site_url_or_the_first_allowed_origin():
    assert _settings(allowed_origins="https://app.example.com,http://localhost:5173").owner_site_url == "https://app.example.com"
    assert _settings(site_url="https://x.example/").owner_site_url == "https://x.example"


def test_no_template_names_a_recipient_or_a_floor():
    import inspect

    import salesai.modules.mail.templates as t
    src = inspect.getsource(t)
    assert not re.search(r"price_floor|floor_paise|owner_phone", src)
