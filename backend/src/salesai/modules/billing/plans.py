"""The plan catalogue: one place that says what each plan costs and includes. Prices are PLACEHOLDERS until the founder sets them
(PRD: "pricing after cost measurement"); changing a number here changes the pricing page, the billing screen and invoices together.
`scripts/export_plans.py` writes the same data to the frontend, and a test fails if the two drift apart. Money is in paise."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

TRIAL_DAYS = 14
TRIAL_PLAN = "growth"                 # a trial gives the full Growth experience, no card needed
GST_RATE_PERCENT = 18
GRACE_DAYS = 7                        # after an unpaid invoice falls due, the assistant keeps running this long


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    tagline: str
    price_month_paise: int
    price_year_paise: int             # yearly billing: two months free
    included_conversations: int | None    # AI-handled conversations per month; None = unlimited
    overage_paise: int                # per extra conversation beyond the included amount
    max_numbers: int | None
    max_products: int | None
    max_team: int | None
    features: list[str] = field(default_factory=list)
    popular: bool = False
    public: bool = True


PLANS: dict[str, Plan] = {p.code: p for p in [
    Plan("starter", "Starter", "For a single counter getting started", 99_900, 999_000, 500, 300, 1, 200, 2,
         ["1 WhatsApp number", "500 AI conversations a month", "Up to 200 products", "2 team members", "Dashboard and daily summary", "Pricing you control, with a private lowest price"]),
    Plan("growth", "Growth", "For a busy shop that lives on WhatsApp", 249_900, 2_499_000, 2_000, 200, 2, 1_000, 5,
         ["Up to 2 WhatsApp numbers", "2,000 AI conversations a month", "Up to 1,000 products", "5 team members", "Analytics and downloadable reports", "Offers and festive discounts", "Priority support"], popular=True),
    Plan("scale", "Scale", "For several counters or branches", 599_900, 5_999_000, 6_000, 150, 3, None, 15,
         ["Up to 3 WhatsApp numbers", "6,000 AI conversations a month", "Unlimited products", "15 team members", "Analytics and downloadable reports", "Onboarding help from our team", "Priority support"]),
    # pilot: shops run by hand for the founder (no charge, no limits). Not offered on the pricing page.
    Plan("pilot", "Pilot", "Founder-run pilot", 0, 0, None, 0, None, None, None, ["No charge during the pilot"], public=False),
]}


def public_plans() -> list[Plan]:
    return [p for p in PLANS.values() if p.public]


def catalogue() -> dict[str, Any]:
    """The JSON the pricing page and billing screen render. Keep this the only place that shapes it."""
    return {"currency": "INR", "gst_percent": GST_RATE_PERCENT, "trial_days": TRIAL_DAYS, "trial_plan": TRIAL_PLAN,
            "plans": [asdict(p) for p in public_plans()]}


def gst_for(subtotal_paise: int) -> int:
    return (subtotal_paise * GST_RATE_PERCENT + 50) // 100        # rounded to the nearest paisa
