"""A QR code as text, for the terminal (ADR 0047). The only place the QR library is used."""
from __future__ import annotations

import io


def qr_text(data: str) -> str:
    """The code drawn with half-block characters (two QR rows per line), with the quiet border a scanner needs. Falls back to plain
    `##` blocks for a console that cannot print them (see `qr_ascii`)."""
    import segno
    out = io.StringIO()
    segno.make(data, error="m").terminal(out=out, compact=True, border=2)
    return out.getvalue()


def qr_ascii(data: str) -> str:
    """The same code in plain ASCII (`##` for dark, spaces for light): larger, but prints on any console."""
    import segno
    q = segno.make(data, error="m")
    border = 2
    width = len(q.matrix[0]) + 2 * border
    blank = " " * (width * 2)
    rows = [blank] * border
    for row in q.matrix:
        rows.append(" " * (border * 2) + "".join("##" if cell else "  " for cell in row) + " " * (border * 2))
    rows += [blank] * border
    return "\n".join(rows) + "\n"


def printable_qr(data: str, encoding: str | None) -> str:
    """Whichever drawing this console can actually print."""
    text = qr_text(data)
    try:
        text.encode(encoding or "utf-8")
        return text
    except (UnicodeEncodeError, LookupError):
        return qr_ascii(data)
