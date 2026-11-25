"""ADR 0052: founder alert mail can leave through the Amazon SES API instead of SMTP. The transport is configuration only; SMTP stays
the default and keeps working. A fake SES client stands in for AWS."""
from __future__ import annotations

from email.message import EmailMessage

import pytest

from salesai.config import Settings
from salesai.modules.alerting.email_sink import EmailSink, ses_check, ses_deliver

FOUNDER = "founder@chatdeal.example"


class FakeSes:
    def __init__(self, fail: Exception | None = None):
        self.sent: list[dict] = []
        self.fail = fail

    def send_email(self, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(kw)
        return {"MessageId": "m-1"}

    def get_send_quota(self):
        return {"Max24HourSend": 200.0}


def settings(**kw) -> Settings:
    base = dict(llm_provider="local", database_url="postgresql://x@h/d", system_database_url="postgresql://x@h/d", pricing_database_url="postgresql://x@h/d",
                console_database_url="postgresql://x@h/d", migration_database_url="postgresql://x@h/d", role="all", master_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", jwt_secret="j" * 32, otp_secret="o" * 32,
                meta_app_secret="m" * 12, meta_verify_token="v" * 12)
    return Settings(**{**base, **kw})


async def test_ses_sink_sends_subject_body_sender_and_recipients():
    ses = FakeSes()
    sink = EmailSink("", 0, sender="alerts@chatdeal.example", to=[FOUNDER, "b@chatdeal.example"], deliver=ses_deliver("ap-south-1", client=ses))
    await sink.send_mail("Subject\nInjected: header", "the body")
    (call,) = ses.sent
    assert call["Source"] == "alerts@chatdeal.example"
    assert call["Destination"]["ToAddresses"] == [FOUNDER, "b@chatdeal.example"]
    assert call["Message"]["Subject"]["Data"] == "Subject Injected: header"       # one line: untrusted text cannot add a header
    assert call["Message"]["Body"]["Text"]["Data"].strip() == "the body"


async def test_a_ses_failure_surfaces_so_the_router_can_retry():
    sink = EmailSink("", 0, sender="a@chatdeal.example", to=[FOUNDER], deliver=ses_deliver("ap-south-1", client=FakeSes(RuntimeError("MessageRejected"))))
    with pytest.raises(RuntimeError, match="MessageRejected"):
        await sink.send_mail("s", "b")


def test_doctor_check_reports_the_limit_and_sends_only_when_asked():
    ses = FakeSes()
    note = ses_check("ap-south-1", "", "", "a@chatdeal.example", [FOUNDER], send=False, client=ses)
    assert "ap-south-1" in note and "200" in note and ses.sent == []
    ses_check("ap-south-1", "", "", "a@chatdeal.example", [FOUNDER], send=True, client=ses)
    assert len(ses.sent) == 1 and ses.sent[0]["Destination"]["ToAddresses"] == [FOUNDER]


def test_ses_is_the_default_and_smtp_is_still_a_choice():
    assert settings().email_transport == "ses"
    s = settings(email_transport="smtp", alert_email_to=FOUNDER, alert_email_from="a@chatdeal.example", smtp_host="smtp.example")
    assert s.email_transport == "smtp" and not s.ses_region
    with pytest.raises(ValueError, match="SMTP_HOST"):
        settings(email_transport="smtp", alert_email_to=FOUNDER, alert_email_from="a@chatdeal.example")


def test_ses_needs_a_region_and_a_sender_but_not_an_smtp_host():
    s = settings(email_transport="ses", ses_region="ap-south-1", alert_email_to=FOUNDER, alert_email_from="a@chatdeal.example")
    assert s.email_transport == "ses" and not s.smtp_host
    with pytest.raises(ValueError, match="SES_REGION"):
        settings(email_transport="ses", alert_email_to=FOUNDER, alert_email_from="a@chatdeal.example")
    with pytest.raises(ValueError, match="ALERT_EMAIL_FROM"):
        settings(email_transport="ses", ses_region="ap-south-1", alert_email_to=FOUNDER)


def test_ses_keys_come_as_a_pair_or_not_at_all():
    settings(email_transport="ses", ses_region="r", alert_email_to=FOUNDER, alert_email_from="a@chatdeal.example")       # role: no keys
    with pytest.raises(ValueError, match="together"):
        settings(email_transport="ses", ses_region="r", ses_access_key_id="AKIA", alert_email_to=FOUNDER, alert_email_from="a@chatdeal.example")


def test_the_message_object_is_what_the_transport_receives():
    got: list[EmailMessage] = []
    sink = EmailSink("", 0, sender="a@chatdeal.example", to=[FOUNDER], deliver=got.append)
    sink._deliver(EmailMessage())
    assert len(got) == 1


# ---------------------------------------------------------------- the delivery record (ADR 0053)

async def test_every_attempt_is_recorded_with_the_ses_message_id_and_no_address(world):
    from salesai.alerts import raise_alert
    from salesai.modules.alerting import AlertRouter

    async with world.rt.db.system_tx() as c:
        await c.execute("DELETE FROM operator_alerts")
        await c.execute("DELETE FROM alert_deliveries")
        await raise_alert(c, "critical", "send_failed", "Outbound message failed")
    ok = EmailSink("", 0, sender="a@chatdeal.example", to=[FOUNDER], deliver=ses_deliver("ap-south-1", client=FakeSes()))
    assert await AlertRouter(world.rt.db, [ok]).dispatch() == 1
    async with world.rt.db.system_tx() as c:
        rows = await (await c.execute("SELECT * FROM alert_deliveries")).fetchall()
    assert len(rows) == 1 and rows[0]["ok"] and rows[0]["sink"] == "email" and rows[0]["kind"] == "alert"
    assert rows[0]["provider_ref"] == "m-1"
    assert FOUNDER not in " ".join(str(v) for v in rows[0].values())


async def test_a_refused_send_is_recorded_as_failed(world):
    from salesai.alerts import raise_alert
    from salesai.modules.alerting import AlertRouter

    async with world.rt.db.system_tx() as c:
        await c.execute("DELETE FROM operator_alerts")
        await c.execute("DELETE FROM alert_deliveries")
        await raise_alert(c, "critical", "send_failed", "Outbound message failed")
    bad = EmailSink("", 0, sender="a@chatdeal.example", to=[FOUNDER], deliver=ses_deliver("r", client=FakeSes(RuntimeError("MessageRejected"))))
    await AlertRouter(world.rt.db, [bad]).dispatch()
    async with world.rt.db.system_tx() as c:
        rows = await (await c.execute("SELECT ok, error FROM alert_deliveries")).fetchall()
    assert [r["ok"] for r in rows] == [False] and "MessageRejected" in rows[0]["error"]
