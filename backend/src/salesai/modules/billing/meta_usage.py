"""Meta message usage per shop (ADR 0026): an estimate of what Meta will bill, from what we actually delivered.

Meta bills per delivered message. Since 1 Oct 2026 each business number gets `free_service_per_number` free service
messages a calendar month; beyond that, service messages cost the utility rate. Rates and the allowance are
configuration (META_RATES_INR, META_FREE_SERVICE_PER_NUMBER) because Meta has changed them twice in 2026.

What counts:
- customer messages WE sent through the API and that reached the customer. An owner reply typed in the WhatsApp
  Business app (a coexistence echo) is not billed by Meta, so it is not counted;
- owner alerts that reached the owner: free-form text is a service message, an approved template a utility one, a
  sign-in code an authentication one.
Who pays follows whose WhatsApp account the number is on: the shop's own (Embedded Signup, it holds its own token) or
the platform's (pilot numbers under our account, FR-ON-3). Alerts sent from the platform's own sender are always ours.

This is an estimate on UTC calendar months; Meta's invoice is the source of truth."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from salesai.db import Database

_CUSTOMER = """
SELECT cv.whatsapp_number_id AS number_id,
       count(*) FILTER (WHERE m.kind <> 'template') AS service,
       count(*) FILTER (WHERE m.kind = 'template')  AS utility
  FROM messages m JOIN conversations cv ON cv.id = m.conversation_id
 WHERE m.direction = 'out' AND m.sent_via = 'api'
   AND COALESCE(m.delivered_at, m.read_at) >= %s AND COALESCE(m.delivered_at, m.read_at) < %s
 GROUP BY 1"""

_OWNER = """
SELECT whatsapp_number_id AS number_id,
       count(*) FILTER (WHERE kind = 'text')                         AS service,
       count(*) FILTER (WHERE kind = 'template' AND purpose <> 'otp') AS utility,
       count(*) FILTER (WHERE purpose = 'otp')                       AS authentication
  FROM owner_messages
 WHERE direction = 'out' AND status IN ('delivered', 'read') AND sent_at >= %s AND sent_at < %s
 GROUP BY 1"""


class MetaUsage:
    def __init__(self, db: Database, *, rates_inr: dict[str, float], free_service_per_number: int):
        self.db, self.rates, self.free = db, rates_inr, free_service_per_number

    def _rate(self, category: str) -> float:
        return float(self.rates.get(category, self.rates["utility"]))

    async def month(self, business_id: uuid.UUID, year: int, month: int) -> dict[str, Any]:
        lo = datetime(year, month, 1, tzinfo=UTC)
        hi = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
        async with self.db.tenant(business_id) as c:
            numbers = await (await c.execute(
                "SELECT id, display_phone, (access_token_enc IS NOT NULL) AS own_account FROM whatsapp_numbers ORDER BY created_at")).fetchall()
            customer = {r["number_id"]: r for r in await (await c.execute(_CUSTOMER, (lo, hi))).fetchall()}
            owner = {r["number_id"]: r for r in await (await c.execute(_OWNER, (lo, hi))).fetchall()}

        rows: list[dict[str, Any]] = []
        pays = {"shop": 0.0, "platform": 0.0}
        for n in numbers:
            cu, ow = customer.get(n["id"], {}), owner.get(n["id"], {})
            service = int(cu.get("service", 0)) + int(ow.get("service", 0))
            utility = int(cu.get("utility", 0)) + int(ow.get("utility", 0))
            auth = int(ow.get("authentication", 0))
            free = min(service, self.free)                       # the allowance is per number, per month
            cost = (service - free) * self._rate("service") + utility * self._rate("utility") + auth * self._rate("authentication")
            payer = "shop" if n["own_account"] else "platform"
            pays[payer] += cost
            rows.append({"number_id": str(n["id"]), "display_phone": n["display_phone"], "payer": payer,
                         "service": service, "utility": utility, "authentication": auth,
                         "free_service": free, "billable_service": service - free, "estimated_cost_inr": round(cost, 4)})

        # The platform sender serves every shop, so its free allowance cannot be split per shop: priced in full here.
        ps = owner.get(None, {})
        p_service, p_util, p_auth = int(ps.get("service", 0)), int(ps.get("utility", 0)), int(ps.get("authentication", 0))
        p_cost = p_service * self._rate("service") + p_util * self._rate("utility") + p_auth * self._rate("authentication")
        pays["platform"] += p_cost
        return {
            "month": f"{year:04d}-{month:02d}",
            "numbers": rows,
            "platform_sender": {"service": p_service, "utility": p_util, "authentication": p_auth, "estimated_cost_inr": round(p_cost, 4)},
            "estimated_cost_inr": {"shop_pays": round(pays["shop"], 4), "platform_pays": round(pays["platform"], 4)},
            "rates_inr": dict(self.rates), "free_service_per_number": self.free,
            "basis": "Estimated from messages delivered in the UTC calendar month. Meta's invoice is the source of truth.",
        }
