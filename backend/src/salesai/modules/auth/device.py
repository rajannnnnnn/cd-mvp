"""What a console sign-in came from, in words a founder can read (ADR 0048). Pure functions: no network, no lookups."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

_BROWSERS = (
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/(\d+)")),
    ("Opera", re.compile(r"OPR/(\d+)")),
    ("Firefox", re.compile(r"(?:Firefox|FxiOS)/(\d+)")),
    ("Chrome", re.compile(r"(?:Chrome|CriOS)/(\d+)")),
    ("Safari", re.compile(r"Version/(\d+)[^ ]* (?:Mobile/\S+ )?Safari/")),
    ("curl", re.compile(r"^curl/([\d.]+)")),
    ("Python", re.compile(r"python-(?:requests|httpx)/([\d.]+)", re.I)),
)
_SYSTEMS = (
    ("iPhone", re.compile(r"iPhone")), ("iPad", re.compile(r"iPad")), ("Android", re.compile(r"Android")),
    ("Windows", re.compile(r"Windows NT")), ("macOS", re.compile(r"Mac OS X|Macintosh")), ("ChromeOS", re.compile(r"CrOS")),
    ("Linux", re.compile(r"Linux|X11")),
)


@dataclass(frozen=True)
class ClientInfo:
    """Everything the console records about where a request came from."""
    ip: str | None = None
    user_agent: str | None = None
    geo: str | None = None


def describe_device(user_agent: str | None) -> str:
    """'Chrome 142 on Windows'; 'Unknown device' when there is nothing to go on."""
    ua = (user_agent or "").strip()
    if not ua:
        return "Unknown device"
    browser = next((f"{name} {m.group(1)}" for name, rx in _BROWSERS if (m := rx.search(ua))), None)
    system = next((name for name, rx in _SYSTEMS if rx.search(ua)), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or (f"Unknown browser on {system}" if system else "Unknown device")


def network_of(ip: str | None) -> str | None:
    """The network an address belongs to for 'same place as last time': /24 for IPv4, /48 for IPv6. None when there is no usable address."""
    if not ip:
        return None
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    return str(ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 48}", strict=False))


_DOCUMENTATION = tuple(ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32"))   # example addresses, never a real private network


def is_private(ip: str | None) -> bool:
    """A home, office or container network (private, loopback, link-local); the documentation ranges used in examples count as ordinary addresses."""
    try:
        a = ipaddress.ip_address(str(ip))
    except ValueError:
        return False
    return a.is_private and not any(a in net for net in _DOCUMENTATION if net.version == a.version)


def where_label(ip: str | None, geo: str | None) -> str:
    """'IN · 203.0.113.9', 'Private network · 172.18.0.1', or just the address; 'Unknown' when there is none."""
    if not ip:
        return "Unknown"
    if geo:
        return f"{geo} · {ip}"
    return f"Private network · {ip}" if is_private(ip) else ip


def clean_geo(value: str | None) -> str | None:
    """A two-letter country code from the proxy's header, or nothing (the header is untrusted text: keep only a plausible code)."""
    v = (value or "").strip().upper()
    return v if re.fullmatch(r"[A-Z]{2}", v) and v not in {"XX", "T1"} else None
