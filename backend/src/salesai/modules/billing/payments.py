"""Payment provider port. The product creates invoices and records payments; how money is collected is a vendor choice
(Razorpay is the natural fit in India and needs a registered entity). Until then `TestGateway` settles instantly and says so
on screen; it is refused in production by configuration."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Checkout:
    provider_ref: str
    mode: str                         # "test" | "live"
    status: str                       # "succeeded" | "pending"
    checkout_url: str | None = None
    instructions: str | None = None


class PaymentProvider(Protocol):
    name: str
    mode: str

    async def charge(self, invoice: dict[str, Any], business: dict[str, Any]) -> Checkout: ...


class TestGateway:
    """Settles every charge immediately. Behaves like a real gateway from the product's point of view (reference, status),
    but no money moves: the UI labels it TEST MODE."""
    __test__ = False
    name = "test"
    mode = "test"

    async def charge(self, invoice: dict[str, Any], business: dict[str, Any]) -> Checkout:      # noqa: ARG002
        return Checkout(provider_ref=f"test_{uuid.uuid4().hex[:16]}", mode="test", status="succeeded")


class ManualProvider:
    """Bank transfer / UPI collected outside the product: the invoice stays open until the platform team marks it paid.
    A realistic way to start charging before a payment gateway account exists."""
    name = "manual"
    mode = "manual"

    def __init__(self, instructions: str):
        self.instructions = instructions

    async def charge(self, invoice: dict[str, Any], business: dict[str, Any]) -> Checkout:      # noqa: ARG002
        return Checkout(provider_ref=f"manual_{invoice['number']}", mode="manual", status="pending", instructions=self.instructions)
