"""`python -m salesai mail-preview [folder]` writes every email template, filled with sample data, as an HTML file plus an index page, so each mail
can be looked at in a browser. It sends nothing and needs no keys."""
from __future__ import annotations

import html
from datetime import UTC, datetime
from pathlib import Path

from salesai.modules.mail import CATALOG, founder_alert_html, founder_digest_html, render

SITE = "https://app.example.com"
FOUNDER = {
    "founder_alert": ("founder", "A critical alert (Meta disconnected a shop's number)"),
    "founder_resolved": ("founder", "The same alert, resolved"),
    "founder_digest": ("founder", "The hourly or daily digest"),
}


def build(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 11, 24, 9, 30, tzinfo=UTC)
    alert = {"id": 41, "severity": "critical", "kind": "number_disconnected", "business_name": "Sharma Mobiles", "message": "WhatsApp number +91 98xxxxxx00 was disconnected",
             "occurrences": 3, "created_at": now, "last_seen_at": now}
    # the plain-text bodies as format.py writes them (kept here so this tool imports no other part of the system)
    body = "\n".join(["CRITICAL — Sharma Mobiles", str(alert["message"]), "",
                     "What it means: This shop's WhatsApp number is cut off, so its customers are not being answered.",
                     "Seen 3 times, first 24 Nov 09:30 UTC, last 24 Nov 09:30 UTC.",
                     "Where to look: Operator console → Tenants, then ask the owner to reconnect · alert #41"]) + "\n"
    rbody = "\n".join(["RESOLVED — Sharma Mobiles", str(alert["message"]), "",
                      "This has been marked resolved. No action needed.",
                      "(It was seen 3 times; first 24 Nov 09:30 UTC.)",
                      "Where to look: Operator console → Alerts · alert #41"]) + "\n"
    subject = "[LOW] ChatDeal daily digest: 2 things to look at"
    pages: list[tuple[str, str, str, str, str]] = []        # (file, purpose, about, subject, html)
    for t in CATALOG.values():
        r = render(t.name, t.sample, SITE)
        pages.append((t.name, t.purpose, t.about, r.subject, r.html))
    pages.append(("founder_alert", "founder", FOUNDER["founder_alert"][1], "[CRITICAL] Sharma Mobiles — number disconnected", founder_alert_html(alert, body)))
    pages.append(("founder_resolved", "founder", FOUNDER["founder_resolved"][1], "[RESOLVED] Sharma Mobiles — number disconnected", founder_alert_html({**alert, "recovered": True}, rbody)))
    pages.append(("founder_digest", "founder", FOUNDER["founder_digest"][1], subject, founder_digest_html(subject, [alert, {**alert, "id": 42, "message": "A reply could not be delivered", "occurrences": 1}])))
    out: list[Path] = []
    rows = []
    for name, purpose, about, subj, page in pages:
        f = folder / f"{name}.html"
        f.write_text(page, encoding="utf-8")
        out.append(f)
        rows.append(f'<tr><td><a href="{html.escape(name)}.html">{html.escape(name)}</a></td><td>{html.escape(purpose)}</td><td>{html.escape(about)}</td><td>{html.escape(subj)}</td></tr>')
    index = folder / "index.html"
    index.write_text('<!doctype html><meta charset="utf-8"><title>ChatDeal mail templates</title>'
                     '<style>body{font:15px system-ui;margin:40px;color:#14201b}td,th{padding:8px 14px;border-bottom:1px solid #e3e9e5;text-align:left}a{color:#0b6b4b;font-weight:600}</style>'
                     '<h1>ChatDeal mail templates</h1><p>Sample data. Nothing is sent.</p><table><tr><th>Template</th><th>Sender</th><th>When</th><th>Subject</th></tr>'
                     + "".join(rows) + "</table>", encoding="utf-8")
    out.append(index)
    return out
