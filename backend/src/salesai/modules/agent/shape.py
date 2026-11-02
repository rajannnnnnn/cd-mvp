"""How a reply is split into WhatsApp messages (ADR 0026; founder: "multi-message is not a strict rule — it is for
long replies, and adapted to the customer's style").

One message by default. Several only when the answer is genuinely long, or when the customer is themselves writing in
short bursts — mirroring their rhythm the way FR-CV-5 mirrors their language and tone. Since 1 Oct 2026 every
delivered message is billed, so this is better for the customer and cheaper at once.

Pure and deterministic: it regroups the writer's parts and never changes a word, so the price checks that already ran
on those parts still hold (INV-2). Thresholds are per-shop settings, not constants."""
from __future__ import annotations

JOIN = "\n"


def shape_reply(parts: list[str], customer_texts: list[str], *, max_parts: int = 3, split_over_chars: int = 320,
                burst_chars: int = 80) -> list[str]:
    parts = [p.strip() for p in parts if p and p.strip()]
    if not parts:
        return []
    if len(parts) == 1 or max_parts <= 1:
        return [JOIN.join(parts)]
    said = [t.strip() for t in customer_texts if t and t.strip()]
    bursty = len(said) >= 2 and all(len(t) <= burst_chars for t in said)
    long = sum(len(p) for p in parts) > split_over_chars
    if not (bursty or long):
        return [JOIN.join(parts)]
    if len(parts) > max_parts:                       # the ceiling: overflow joins the last message, nothing is dropped
        parts = [*parts[: max_parts - 1], JOIN.join(parts[max_parts - 1:])]
    return parts
