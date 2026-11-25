"""The look of every ChatDeal email, in one place. Table layout and inline styles, because mail clients ignore most of modern CSS; a small
<style> block adds dark mode and the phone width for the clients that read it. Every value that comes from outside (a shop's name, a ticket
subject) goes through `esc`, so it can never add markup. Nothing here knows what any mail says: see templates.py."""
from __future__ import annotations

import html as _html
from collections.abc import Iterable
from typing import Any

BRAND = "#0b6b4b"          # ChatDeal green (the same as the app's WhatsApp header)
BRAND_SOFT = "#e7f3ee"
INK = "#14201b"
MUTED = "#5b6b64"
LINE = "#e3e9e5"
PAGE = "#f3f6f4"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
TONES = {"critical": "#b42318", "high": "#c4620b", "medium": "#a16207", "low": "#475467", "ok": BRAND, "info": BRAND}


class Html(str):
    """Markup that was built here and is safe to place as it is."""


def esc(value: Any) -> Html:
    return value if isinstance(value, Html) else Html(_html.escape(str(value), quote=True))


def _join(parts: Iterable[Any]) -> Html:
    return Html("".join(esc(p) for p in parts))


def h1(text: Any) -> Html:
    return Html(f'<h1 class="ink" style="margin:0 0 12px;font:700 24px/1.3 {FONT};color:{INK}">{esc(text)}</h1>')


def p(text: Any, *, muted: bool = False, small: bool = False) -> Html:
    colour, size = (MUTED, "13px") if (muted or small) else (INK, "16px")
    cls = "muted" if (muted or small) else "ink"
    return Html(f'<p class="{cls}" style="margin:0 0 16px;font:400 {size}/1.6 {FONT};color:{colour}">{esc(text)}</p>')


def button(label: Any, url: str) -> Html:
    """A button that works in every client (a padded table cell, not CSS on a link)."""
    return Html(f'<table role="presentation" cellspacing="0" cellpadding="0" style="margin:8px 0 24px"><tr><td style="border-radius:10px;background:{BRAND}">'
                f'<a href="{esc(url)}" style="display:inline-block;padding:13px 26px;font:600 15px/1 {FONT};color:#ffffff;text-decoration:none;border-radius:10px">{esc(label)}</a>'
                f'</td></tr></table>')


def chip(text: Any, tone: str = "info") -> Html:
    colour = TONES.get(tone, BRAND)
    return Html(f'<span style="display:inline-block;margin:0 0 14px;padding:4px 10px;border-radius:999px;background:{colour};'
                f'font:700 11px/1.2 {FONT};letter-spacing:.06em;color:#ffffff;text-transform:uppercase">{esc(text)}</span>')


def facts(rows: list[tuple[str, Any]]) -> Html:
    """A small two-column table of label and value: the receipt-like part of a mail."""
    cells = "".join(
        f'<tr><td class="muted" style="padding:9px 0;border-top:1px solid {LINE};font:400 13px/1.5 {FONT};color:{MUTED};width:38%;vertical-align:top">{esc(k)}</td>'
        f'<td class="ink" style="padding:9px 0;border-top:1px solid {LINE};font:600 14px/1.5 {FONT};color:{INK};vertical-align:top">{esc(v)}</td></tr>' for k, v in rows)
    return Html(f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:4px 0 22px;border-bottom:1px solid {LINE}">{cells}</table>')


def note(text: Any) -> Html:
    """A soft highlighted box for the one thing to notice."""
    return Html(f'<div class="soft" style="margin:0 0 20px;padding:14px 16px;border-radius:10px;background:{BRAND_SOFT};font:400 14px/1.6 {FONT};color:{INK}">{esc(text)}</div>')


def code_box(code: Any) -> Html:
    """The one-time code, large and spaced, easy to read and to copy."""
    return Html(f'<div class="soft" style="margin:6px 0 22px;padding:18px 16px;border-radius:12px;background:{BRAND_SOFT};text-align:center;'
                f'font:700 34px/1.2 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.28em;color:{INK}">{esc(code)}</div>')


def bullets(items: list[Any]) -> Html:
    lis = "".join(f'<li style="margin:0 0 8px">{esc(i)}</li>' for i in items)
    return Html(f'<ul class="ink" style="margin:0 0 18px;padding-left:20px;font:400 15px/1.55 {FONT};color:{INK}">{lis}</ul>')


def shell(*, title: str, preheader: str, body: Iterable[Any], footer: Iterable[str] = (), brand: str = "ChatDeal") -> str:
    """The full page: a quiet header with the brand, a white card, and a footer. `preheader` is the grey preview line inboxes show next to the subject."""
    foot = "".join(f'<p class="muted" style="margin:0 0 6px;font:400 12px/1.6 {FONT};color:{MUTED}">{esc(line)}</p>' for line in footer)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        f'<title>{esc(title)}</title><style>'
        '@media (max-width:620px){.card{padding:26px 20px !important}.wrap{padding:16px 10px !important}}'
        '@media (prefers-color-scheme:dark){.page{background:#0f1512 !important}.card{background:#18211d !important}'
        '.ink{color:#eef3f0 !important}.muted{color:#9fb0a8 !important}.soft{background:#1f3a2f !important;color:#eef3f0 !important}}'
        f'</style></head><body class="page" style="margin:0;padding:0;background:{PAGE}">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:{PAGE}">{esc(preheader)}</div>'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" class="page" style="background:{PAGE}"><tr><td align="center" class="wrap" style="padding:32px 16px">'
        '<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px">'
        f'<tr><td style="padding:0 4px 16px"><span style="font:800 20px/1 {FONT};color:{BRAND};letter-spacing:-.01em">{esc(brand)}</span></td></tr>'
        f'<tr><td class="card" style="background:#ffffff;border-radius:16px;padding:36px 36px 20px;border:1px solid {LINE}">{_join(body)}</td></tr>'
        f'<tr><td style="padding:20px 8px 0">{foot}</td></tr>'
        '</table></td></tr></table></body></html>')
