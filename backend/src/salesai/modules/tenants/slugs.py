"""Business slugs: the public, human-friendly URL of a shop (/app/sharma-sarees). Lowercase words joined by '-',
3-40 characters, unique, and never one of the platform's own path segments."""
from __future__ import annotations

import re
import unicodedata

from salesai.db import Conn

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
RESERVED = frozenset({
    "app", "api", "webhooks", "webhook", "pricing", "login", "signup", "register", "onboarding", "billing", "settings", "privacy", "terms",
    "data-deletion", "contact", "about", "admin", "operator", "static", "assets", "public", "health", "healthz", "readyz", "metrics", "docs",
    "status", "blog", "help", "support", "www", "mail", "config", "index", "home", "start", "demo", "offer", "careers", "security", "legal",
})


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:34].strip("-")
    return s if len(s) >= 3 else (s + "-shop").strip("-") if s else "shop"


def valid_slug(slug: str) -> str | None:
    """None when usable, otherwise a short reason for the person choosing it."""
    if not SLUG_RE.match(slug):
        return "Use 3-40 lowercase letters, numbers or dashes, starting and ending with a letter or number."
    if slug in RESERVED:
        return "That name is reserved. Please pick another."
    return None


async def unique_slug(c: Conn, name: str, *, exclude_business: object | None = None) -> str:
    base = slugify(name)
    if base in RESERVED:
        base = f"{base}-shop"
    cand, n = base, 1
    while True:
        row = await (await c.execute("SELECT id FROM businesses WHERE slug=%s", (cand,))).fetchone()
        if row is None or (exclude_business is not None and str(row["id"]) == str(exclude_business)):
            return cand
        n += 1
        cand = f"{base[: 38 - len(str(n))]}-{n}"
