"""Every email ChatDeal sends, in ONE place. Each template turns a small dict of facts into a subject, a plain-text body and an HTML body that
share one design (layout.py). To read or change the wording of any mail, this is the only file. `python -m salesai mail-preview` writes them all
to a folder so you can see each one.

Rules every template keeps: it never carries a ticket's text, a customer's message, a price floor or a recipient's address (a ticket mail links to
the app; the address is read when sending); subjects are one line; everything that came from outside is escaped by the layout helpers."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from salesai.modules.mail import layout as L

Purpose = str                    # founder | billing | support | account | updates
CLIENT_PURPOSES = ("billing", "support", "account", "updates")


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class Template:
    name: str
    purpose: Purpose
    render: Callable[[dict[str, Any], str], Rendered]       # (data, site_url) -> mail
    sample: dict[str, Any]                                    # what the preview fills in
    about: str                                                # one line: when this mail is sent


# ---------------------------------------------------------------- small helpers
def inr(paise: Any) -> str:
    """₹1,23,456.00 with the Indian grouping."""
    n = int(paise or 0)
    sign, n = ("-" if n < 0 else ""), abs(n)
    rupees, p = divmod(n, 100)
    s = str(rupees)
    head, tail = s[:-3], s[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}₹{','.join([*groups, tail])}.{p:02d}"


def day(v: Any) -> str:
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return str(v)
    return str(v.strftime("%d %b %Y")) if isinstance(v, (datetime, date)) else ""


def one_line(s: Any) -> str:
    return " ".join(str(s).split())[:150]


def _url(site_url: str, path: str) -> str:
    return site_url.rstrip("/") + path


def _hello(data: dict[str, Any]) -> str:
    name = one_line(data.get("name") or "")
    return f"Hi {name}," if name else "Hello,"


FOOTER_SHOP = "You are receiving this because it is the billing and support address for {shop} on ChatDeal. Change it any time in Settings."


def _footer(data: dict[str, Any]) -> list[str]:
    return [FOOTER_SHOP.format(shop=one_line(data.get("shop") or "your shop")), "ChatDeal · answers and converts your customers on WhatsApp."]


# ---------------------------------------------------------------- billing
def payment_confirmed(d: dict[str, Any], site: str) -> Rendered:
    shop, num, total = one_line(d["shop"]), one_line(d["number"]), inr(d["total_paise"])
    plan = str(d.get("plan") or "").title()
    link = _url(site, "/billing")
    rows = [("Invoice", num), ("Amount paid", total), ("Plan", plan), ("Period", f"{day(d.get('period_start'))} to {day(d.get('period_end'))}"), ("Paid on", day(d.get("paid_at")))]
    text = "\n".join([_hello(d), "", f"We received your payment of {total} for {shop}. Thank you.", "", *[f"{k}: {v}" for k, v in rows], "",
                      f"Your invoice is here: {link}", "", "If anything looks wrong, reply to this email and we will sort it out.", "", "ChatDeal"]) + "\n"
    body = [L.chip("Payment received", "ok"), L.h1(f"Thank you, we received {total}"), L.p(_hello(d)),
            L.p(f"Your payment for {shop} went through and your plan is active. Here is your receipt."), L.facts(rows),
            L.button("View invoice", link), L.p("If anything looks wrong, just reply to this email and we will sort it out.", muted=True)]
    return Rendered(f"Payment received: {num}", text, L.shell(title="Payment received", preheader=f"{total} received for {shop}. Invoice {num}.", body=body, footer=_footer(d)))


def payment_failed(d: dict[str, Any], site: str) -> Rendered:
    shop, num, total = one_line(d["shop"]), one_line(d["number"]), inr(d["total_paise"])
    link = _url(site, "/billing")
    text = "\n".join([_hello(d), "", f"We could not collect {total} for invoice {num} ({shop}).", "",
                      "Your assistant keeps answering customers for now. Please pay to avoid an interruption:", link, "",
                      "If you already paid, reply to this email with the payment reference and we will match it.", "", "ChatDeal"]) + "\n"
    body = [L.chip("Action needed", "high"), L.h1("We could not collect your payment"), L.p(_hello(d)),
            L.p(f"Invoice {num} for {shop} ({total}) is still unpaid. Your assistant keeps answering customers for now, but please pay soon to avoid an interruption."),
            L.facts([("Invoice", num), ("Amount due", total)]), L.button("Pay now", link),
            L.p("Already paid? Reply to this email with the payment reference and we will match it.", muted=True)]
    return Rendered(f"Action needed: payment for {num}", text, L.shell(title="Payment needed", preheader=f"{total} is due on invoice {num}.", body=body, footer=_footer(d)))


def trial_ending(d: dict[str, Any], site: str) -> Rendered:
    shop, days = one_line(d["shop"]), int(d.get("days_left") or 0)
    when = "today" if days <= 0 else ("tomorrow" if days == 1 else f"in {days} days")
    link = _url(site, "/billing")
    text = "\n".join([_hello(d), "", f"Your free trial for {shop} ends {when} ({day(d.get('ends_on'))}).", "",
                      "To keep your assistant answering and following up with customers, choose a plan:", link, "", "ChatDeal"]) + "\n"
    body = [L.chip("Free trial", "info"), L.h1(f"Your trial ends {when}"), L.p(_hello(d)),
            L.p(f"Your free trial for {shop} ends on {day(d.get('ends_on'))}. Choose a plan to keep your assistant answering and following up with customers without a break."),
            L.button("Choose a plan", link)]
    return Rendered(f"Your ChatDeal trial ends {when}", text, L.shell(title="Trial ending", preheader=f"Choose a plan to keep {shop} running.", body=body, footer=_footer(d)))


# ---------------------------------------------------------------- support
def ticket_received(d: dict[str, Any], site: str) -> Rendered:
    shop, num, subj = one_line(d["shop"]), d["number"], one_line(d["subject"])
    link = _url(site, "/settings?tab=support")
    text = "\n".join([_hello(d), "", f"We received your request #{num}: {subj}", "", "Our team will reply in the app, and we will email you when we do.",
                      f"See it here: {link}", "", "ChatDeal Support"]) + "\n"
    body = [L.chip("Request received", "info"), L.h1(f"We have your request #{num}"), L.p(_hello(d)),
            L.p("Thanks for writing to us. Our team will reply inside the app, and we will email you the moment we do."),
            L.facts([("Request", f"#{num}"), ("Subject", subj), ("Shop", shop)]), L.button("Open your request", link)]
    return Rendered(f"We received your request #{num}", text, L.shell(title="Request received", preheader=f"Request #{num}: {subj}", body=body, footer=_footer(d)))


def ticket_replied(d: dict[str, Any], site: str) -> Rendered:
    shop, num, subj = one_line(d["shop"]), d["number"], one_line(d["subject"])
    link = _url(site, "/settings?tab=support")
    text = "\n".join([_hello(d), "", f"Our team replied to your request #{num}: {subj}", "", f"Read the reply and answer in the app: {link}", "", "ChatDeal Support"]) + "\n"
    body = [L.chip("New reply", "ok"), L.h1(f"We replied to request #{num}"), L.p(_hello(d)),
            L.p("Our team has answered your request. For your privacy the reply stays in the app, where you can read it and write back."),
            L.facts([("Request", f"#{num}"), ("Subject", subj), ("Shop", shop)]), L.button("Read the reply", link)]
    return Rendered(f"New reply on request #{num}", text, L.shell(title="New reply", preheader=f"We replied to request #{num}.", body=body, footer=_footer(d)))


# ---------------------------------------------------------------- account
def deletion_requested(d: dict[str, Any], site: str) -> Rendered:
    shop, on = one_line(d["shop"]), day(d.get("delete_on"))
    link = _url(site, "/settings")
    text = "\n".join([_hello(d), "", f"We received a request to delete {shop} and all its data. It will be erased on {on}.", "",
                      f"If this was not you, or you changed your mind, cancel it before then in Settings: {link}", "", "ChatDeal"]) + "\n"
    body = [L.chip("Account", "high"), L.h1("Your shop is scheduled for deletion"), L.p(_hello(d)),
            L.p(f"We received a request to delete {shop} and all of its data. It will be erased on {on}."),
            L.note("Not you, or changed your mind? You can cancel the deletion before that date."), L.button("Open Settings", link)]
    return Rendered(f"{shop} will be deleted on {on}", text, L.shell(title="Deletion scheduled", preheader=f"Erased on {on}. You can cancel until then.", body=body, footer=_footer(d)))


# ---------------------------------------------------------------- access (the console's own sign-in codes)
def login_code(d: dict[str, Any], site: str) -> Rendered:       # noqa: ARG001
    code, mins = one_line(d["code"]), int(d.get("minutes") or 10)
    where, device = one_line(d.get("where") or "an unknown place"), one_line(d.get("device") or "an unknown device")
    text = "\n".join([f"Your ChatDeal console sign-in code is {code}", "", f"It works once and expires in {mins} minutes.",
                      f"Asked for from {device}, {where}.", "",
                      "If this was not you, do not enter it. Someone knows your authenticator code: change FOUNDER_TOTP_SECRET and restart the console.", "", "ChatDeal"]) + "\n"
    body = [L.chip("Sign-in code", "info"), L.h1("Your console sign-in code"), L.p("Enter this code in the console to finish signing in."), L.code_box(code),
            L.p(f"It works once and expires in {mins} minutes."), L.facts([("Asked for from", f"{device}, {where}")]),
            L.note("Not you? Do not enter it. Someone knows your authenticator code: change FOUNDER_TOTP_SECRET and restart the console.")]
    return Rendered("Your ChatDeal sign-in code", text, L.shell(title="Sign-in code", preheader=f"Your code is {code}. It expires in {mins} minutes.", body=body,
                                                                 footer=["Sent because someone entered the founder's authenticator code at the ChatDeal console."]))


# ---------------------------------------------------------------- founder (the platform team's own alerts)
def founder_alert_html(alert: dict[str, Any], text: str) -> str:
    """The same words as the plain-text alert (format.email_parts), laid out. `text` is that body."""
    recovered = bool(alert.get("recovered"))
    tone = "ok" if recovered else str(alert.get("severity") or "low")
    who = str(alert.get("business_name") or "Platform")
    lines = [ln for ln in text.splitlines()]
    msg = lines[1] if len(lines) > 1 else ""
    rest = [ln for ln in lines[2:] if ln.strip()]
    rows: list[tuple[str, Any]] = []
    for ln in rest:
        k, _, v = ln.partition(": ")
        rows.append((k, v) if v else ("", ln))
    body: list[Any] = [L.chip("Resolved" if recovered else str(alert.get("severity") or ""), tone), L.h1(who), L.p(msg)]
    body.append(L.facts([(k or "Note", v) for k, v in rows]) if rows else "")
    return L.shell(title=f"{who}: {msg}"[:120], preheader=msg[:140], body=body, footer=["ChatDeal platform alert. It never contains customer messages or prices."])


def founder_digest_html(subject: str, alerts: list[dict[str, Any]]) -> str:
    items = [(str(a.get("business_name") or "Platform"), f"{a['message']}" + (f" (×{int(a['occurrences'])})" if int(a.get("occurrences") or 1) > 1 else "") + f" · alert #{a['id']}") for a in alerts]
    return L.shell(title=subject, preheader=f"{len(alerts)} to look at.", body=[L.chip("Digest", "low"), L.h1(f"{len(alerts)} thing{'s' if len(alerts) != 1 else ''} to look at"), L.facts(items),
                                                                              L.p("Where to look: Operator console → Alerts.", muted=True)],
                   footer=["ChatDeal platform alert digest."])


# ---------------------------------------------------------------- the catalogue
_SHOP = {"shop": "Sharma Mobiles", "name": "Rajesh"}
CATALOG: dict[str, Template] = {t.name: t for t in [
    Template("payment_confirmed", "billing", payment_confirmed, {**_SHOP, "number": "INV-2611-000042", "total_paise": 117_800_00, "plan": "growth",
                                                                   "period_start": "2026-11-24", "period_end": "2026-12-24", "paid_at": "2026-11-24"}, "A payment for an invoice was received"),
    Template("payment_failed", "billing", payment_failed, {**_SHOP, "number": "INV-2611-000042", "total_paise": 117_800_00}, "A payment could not be collected"),
    Template("trial_ending", "billing", trial_ending, {**_SHOP, "days_left": 3, "ends_on": "2026-11-27"}, "The free trial is about to end"),
    Template("ticket_received", "support", ticket_received, {**_SHOP, "number": 128, "subject": "My WhatsApp number shows disconnected"}, "A shop opened a support request"),
    Template("ticket_replied", "support", ticket_replied, {**_SHOP, "number": 128, "subject": "My WhatsApp number shows disconnected"}, "Support answered a request"),
    Template("login_code", "access", login_code, {"code": "48291736", "minutes": 10, "where": "IN · 203.0.113.9", "device": "Chrome 142 on Windows"},
             "The console asks for an emailed sign-in code (sent directly, not through the outbox)"),
    Template("deletion_requested", "account", deletion_requested, {**_SHOP, "delete_on": "2027-01-23"}, "A shop owner asked to delete the shop"),
]}


def render(template: str, data: dict[str, Any], site_url: str) -> Rendered:
    """The one entry point. An unknown name or a missing field raises, so a mistake is caught in tests and never sends a half-filled mail."""
    t = CATALOG[template]
    return t.render(data, site_url)


def purpose_of(template: str) -> str:
    return CATALOG[template].purpose
