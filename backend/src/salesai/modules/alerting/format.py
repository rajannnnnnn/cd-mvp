"""What an alert says. Plain words for a founder reading on a phone: which shop, what happened, what it means, where to look.
Never customer message content or floor prices: only the cause line the raising code wrote and the counts the table keeps."""
from __future__ import annotations

from typing import Any

ICON = {"low": "ℹ️", "medium": "⚠️", "high": "🔶", "critical": "🚨"}
CONSOLE = "Operator console → Alerts"
HELP: dict[str, tuple[str, str]] = {      # kind -> (what it means, where to look)
    "number_disconnected": ("This shop's WhatsApp number is cut off, so its customers are not being answered.", "Operator console → Tenants, then ask the owner to reconnect"),
    "quality_drop": ("Meta lowered this number's quality rating; a red rating can lead to sending limits.", "Operator console → Tenants → the number's quality"),
    "dead_letter": ("Work the platform gave up on after retries; some messages may not have been handled.", "Operator console → Queues"),
    "turn_failed": ("The assistant failed to answer a customer after retries.", "Operator console → Alerts, then the shop's conversation list"),
    "send_failed": ("A reply could not be delivered to a customer.", "Operator console → Alerts"),
    "delivery_failed": ("WhatsApp reported that a message did not reach the customer.", "Operator console → Alerts"),
    "unknown_number": ("A webhook arrived for a WhatsApp number we have no shop for.", "Operator console → Alerts; check Meta's app settings"),
    "account_event_unmatched": ("Meta sent an account notice we could not match to a shop.", "Operator console → Alerts"),
    "payment_overdue": ("A subscription invoice is overdue.", "Operator console → Billing"),
    "subscription_lapsed": ("A subscription ended.", "Operator console → Billing"),
    "support_ticket": ("A shop owner asked for help.", "Operator console → Support"),
}


def _who(alert: dict[str, Any]) -> str:
    return str(alert.get("business_name") or "Platform")


def alert_text(alert: dict[str, Any]) -> str:
    """One line: what chat sinks and the WhatsApp template carry."""
    who = f" [{alert['business_name']}]" if alert.get("business_name") else ""
    n = int(alert.get("occurrences") or 1)
    count = f" (×{n})" if n > 1 else ""
    if alert.get("recovered"):
        return f"✅ RESOLVED{who}: {alert['message']}"
    return f"{ICON.get(alert['severity'], '')} {alert['severity'].upper()}{who}: {alert['message']}{count}"


def _when(ts: Any) -> str:
    return ts.strftime("%d %b %H:%M UTC") if ts else "?"


def email_parts(alert: dict[str, Any]) -> tuple[str, str]:
    """(subject, body) for one alert, or for its recovery notice when `alert["recovered"]`."""
    who, msg, aid = _who(alert), str(alert["message"]), alert["id"]
    tag = "RESOLVED" if alert.get("recovered") else str(alert["severity"]).upper()
    subject = f"[{tag}] {who} — {msg}"
    meaning, where = HELP.get(alert["kind"], ("Something the platform did not expect.", CONSOLE))
    n = int(alert.get("occurrences") or 1)
    lines = [f"{tag} — {who}", msg, ""]
    if alert.get("recovered"):
        lines += ["This has been marked resolved. No action needed.", f"(It was seen {n} time{'s' if n != 1 else ''}; first {_when(alert.get('created_at'))}.)"]
    else:
        lines += [f"What it means: {meaning}",
                  f"Seen {n} times, first {_when(alert.get('created_at'))}, last {_when(alert.get('last_seen_at'))}." if n > 1
                  else f"Seen once, at {_when(alert.get('created_at'))}."]
    lines += [f"Where to look: {where} · alert #{aid}"]
    return subject, "\n".join(lines) + "\n"


def digest_parts(severity: str, alerts: list[dict[str, Any]]) -> tuple[str, str]:
    """One email for a group of Medium (hourly) or Low (daily) alerts."""
    n = len(alerts)
    what = "daily digest" if severity == "low" else "hourly rollup"
    subject = f"[{severity.upper()}] ChatDeal {what}: {n} thing{'s' if n != 1 else ''} to look at"
    lines = [subject, ""]
    for a in alerts:
        c = int(a.get("occurrences") or 1)
        lines.append(f"- {_who(a)}: {a['message']}" + (f" (×{c})" if c > 1 else "") + f" — alert #{a['id']}")
    lines += ["", f"Where to look: {CONSOLE}"]
    return subject, "\n".join(lines) + "\n"
