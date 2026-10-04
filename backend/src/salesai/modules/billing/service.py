"""Subscriptions, usage, invoices and payments.

State machine (subscriptions.status):  trialing -> active (first payment) -> past_due (unpaid invoice past its due date) -> canceled.
`pilot` shops are run by hand: no charge, no limits. A trial or subscription that lapses switches the assistant off (reversibly:
the shop's data is untouched) and paying switches it back on. All changes are idempotent so the scheduler can run them freely."""
from __future__ import annotations

import calendar
import html
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from salesai.db import Database, jsonb, required
from salesai.modules.billing.payments import PaymentProvider
from salesai.modules.billing.plans import GRACE_DAYS, PLANS, TRIAL_DAYS, TRIAL_PLAN, Plan, gst_for

INVOICE_DUE_DAYS = 3


class BillingError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def add_months(d: datetime, n: int) -> datetime:
    m = d.month - 1 + n
    y, mo = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=mo, day=min(d.day, calendar.monthrange(y, mo)[1]))


def period_end(start: datetime, interval: str) -> datetime:
    return add_months(start, 12 if interval == "year" else 1)


def price_for(plan: Plan, interval: str) -> int:
    return plan.price_year_paise if interval == "year" else plan.price_month_paise


def _lines_total(lines: list[dict[str, Any]]) -> int:
    return sum(int(x["amount_paise"]) for x in lines)


class BillingService:
    def __init__(self, db: Database, provider: PaymentProvider, *, seller: dict[str, str] | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.db, self.provider = db, provider
        self.seller = seller or {"name": "[Company name]", "address": "[Registered address]", "gstin": "[GSTIN]"}
        self.now = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ subscriptions
    async def start_trial(self, business_id: uuid.UUID) -> None:
        now = self.now()
        async with self.db.system_tx() as c:
            await c.execute(
                """INSERT INTO subscriptions (business_id, plan, status, trial_ends_at) VALUES (%s,%s,'trialing',%s)
                   ON CONFLICT (business_id) DO NOTHING""", (business_id, TRIAL_PLAN, now + timedelta(days=TRIAL_DAYS)))

    async def _sub(self, c: Any, business_id: uuid.UUID) -> dict[str, Any]:
        row = await (await c.execute("SELECT * FROM subscriptions WHERE business_id=%s", (business_id,))).fetchone()
        if row is None:         # shops created by hand (operator, demo data) are pilots until someone chooses a plan
            row = await (await c.execute(
                "INSERT INTO subscriptions (business_id, plan, status) VALUES (%s,'pilot','pilot') ON CONFLICT (business_id) DO UPDATE SET plan=subscriptions.plan RETURNING *",
                (business_id,))).fetchone()
        return required(row, "subscription")

    async def usage(self, business_id: uuid.UUID, start: datetime, end: datetime) -> dict[str, int]:
        async with self.db.tenant(business_id) as c:
            conv = (await (await c.execute(
                "SELECT count(DISTINCT conversation_id) AS n FROM turns WHERE created_at >= %s AND created_at < %s AND status IN ('planned','sent')", (start, end))).fetchone())["n"]
            products = (await (await c.execute("SELECT count(*) AS n FROM products WHERE active")).fetchone())["n"]
            numbers = (await (await c.execute("SELECT count(*) AS n FROM whatsapp_numbers")).fetchone())["n"]
            team = (await (await c.execute("SELECT count(*) AS n FROM business_users")).fetchone())["n"]
        return {"conversations": conv, "products": products, "numbers": numbers, "team": team}

    async def view(self, business_id: uuid.UUID) -> dict[str, Any]:
        now = self.now()
        async with self.db.system_tx() as c:
            sub = await self._sub(c, business_id)
            open_inv = await (await c.execute(
                "SELECT id, number, total_paise, due_at FROM invoices WHERE business_id=%s AND status='open' ORDER BY issued_at DESC LIMIT 1", (business_id,))).fetchone()
            biz = required(await (await c.execute("SELECT created_at, ai_enabled, onboarding FROM businesses WHERE id=%s", (business_id,))).fetchone(), "business")
        plan = PLANS[sub["plan"]]
        if sub["status"] == "active" or sub["status"] == "past_due":
            start, end = sub["current_period_start"], sub["current_period_end"]
        elif sub["status"] == "trialing":
            start, end = sub["created_at"], now + timedelta(seconds=1)
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now + timedelta(seconds=1)
        use = await self.usage(business_id, start or now, end or now)
        extra = max(0, use["conversations"] - plan.included_conversations) if plan.included_conversations is not None else 0
        trial_days_left = max(0, (sub["trial_ends_at"] - now).days + (1 if (sub["trial_ends_at"] - now).seconds else 0)) if sub["status"] == "trialing" and sub["trial_ends_at"] else None
        state = sub["status"]
        if state == "canceled":
            state = "trial_ended" if sub["current_period_end"] is None else "canceled"
        return {
            "status": sub["status"], "state": state, "plan": plan.code, "plan_name": plan.name, "interval": sub["billing_interval"],
            "trial_ends_at": sub["trial_ends_at"], "trial_days_left": trial_days_left,
            "current_period_start": sub["current_period_start"], "current_period_end": sub["current_period_end"],
            "cancel_at_period_end": sub["cancel_at_period_end"], "pending_plan": sub["pending_plan"], "pending_interval": sub["pending_interval"],
            "usage": {**use, "included_conversations": plan.included_conversations, "extra_conversations": extra, "overage_estimate_paise": extra * plan.overage_paise,
                      "period_start": start, "period_end": end},
            "limits": {"numbers": plan.max_numbers, "products": plan.max_products, "team": plan.max_team},
            "open_invoice": dict(open_inv) if open_inv else None,
            "assistant_paused_by_billing": bool((biz["onboarding"] or {}).get("billing_paused")),
            "payment_mode": self.provider.mode,
        }

    async def subscribe(self, business_id: uuid.UUID, plan_code: str, interval: str) -> dict[str, Any]:
        plan = PLANS.get(plan_code)
        if plan is None or not plan.public:
            raise BillingError("unknown_plan", "That plan does not exist.", 404)
        if interval not in ("month", "year"):
            raise BillingError("invalid_interval", "Choose monthly or yearly billing.", 422)
        now = self.now()
        async with self.db.system_tx() as c:
            sub = await self._sub(c, business_id)
            await c.execute("SELECT 1 FROM subscriptions WHERE business_id=%s FOR UPDATE", (business_id,))
            existing = await (await c.execute("SELECT * FROM invoices WHERE business_id=%s AND status='open' AND plan=%s AND billing_interval=%s AND period_start <= %s ORDER BY issued_at DESC LIMIT 1",
                                              (business_id, plan.code, interval, now))).fetchone()
            active = sub["status"] in ("active", "past_due") and sub["current_period_end"] and sub["current_period_end"] > now
            if active and sub["plan"] == plan.code and sub["billing_interval"] == interval and not sub["cancel_at_period_end"]:
                raise BillingError("already_on_plan", "You are already on this plan.")
            if active and not sub["cancel_at_period_end"] and PLANS[sub["plan"]].price_month_paise >= plan.price_month_paise and sub["plan"] != "pilot":
                # a downgrade, or a different interval of the same plan: takes effect at the end of the paid period
                await c.execute("UPDATE subscriptions SET pending_plan=%s, pending_interval=%s WHERE business_id=%s", (plan.code, interval, business_id))
                return {"scheduled": True, "invoice": None}
            if existing:
                return {"scheduled": False, "invoice": self._inv(existing)}
            lines = [{"description": f"{plan.name} plan, {'yearly' if interval == 'year' else 'monthly'}", "quantity": 1,
                      "unit_paise": price_for(plan, interval), "amount_paise": price_for(plan, interval)}]
            if active and sub["plan"] != "pilot":      # upgrade: credit the unused part of what was already paid for
                cur = PLANS[sub["plan"]]
                total = (sub["current_period_end"] - sub["current_period_start"]).total_seconds()
                left = max(0.0, (sub["current_period_end"] - now).total_seconds())
                credit = int(price_for(cur, sub["billing_interval"]) * left / total) if total > 0 else 0
                if credit:
                    lines.append({"description": f"Credit for unused time on {cur.name}", "quantity": 1, "unit_paise": -credit, "amount_paise": -credit})
            inv = await self._create_invoice(c, business_id, plan.code, interval, now, period_end(now, interval), lines)
            return {"scheduled": False, "invoice": self._inv(inv)}

    async def _create_invoice(self, c: Any, business_id: uuid.UUID, plan: str, interval: str, start: datetime, end: datetime, lines: list[dict[str, Any]]) -> dict[str, Any]:
        subtotal = max(0, _lines_total(lines))
        gst = gst_for(subtotal)
        seq = (await (await c.execute("SELECT nextval('invoice_number_seq') AS n")).fetchone())["n"]
        now = self.now()
        number = f"INV-{now:%y%m}-{seq:06d}"
        return required(await (await c.execute(
            """INSERT INTO invoices (business_id, number, plan, billing_interval, period_start, period_end, lines, subtotal_paise, gst_paise, total_paise, issued_at, due_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (business_id, number, plan, interval, start, end, jsonb(lines), subtotal, gst, subtotal + gst, now, now + timedelta(days=INVOICE_DUE_DAYS)))).fetchone(), "invoice")

    @staticmethod
    def _inv(r: dict[str, Any]) -> dict[str, Any]:
        return {k: r[k] for k in ("id", "number", "status", "plan", "billing_interval", "period_start", "period_end", "lines", "subtotal_paise",
                                  "gst_paise", "total_paise", "currency", "issued_at", "due_at", "paid_at")}

    async def invoices(self, business_id: uuid.UUID) -> list[dict[str, Any]]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute("SELECT * FROM invoices WHERE business_id=%s ORDER BY issued_at DESC LIMIT 100", (business_id,))).fetchall()
        return [self._inv(r) for r in rows]

    async def invoice(self, business_id: uuid.UUID, invoice_id: uuid.UUID) -> dict[str, Any]:
        async with self.db.system_tx() as c:
            r = await (await c.execute("SELECT * FROM invoices WHERE business_id=%s AND id=%s", (business_id, invoice_id))).fetchone()
        if r is None:
            raise BillingError("not_found", "Invoice not found.", 404)
        return self._inv(r)

    # ------------------------------------------------------------------ paying
    async def pay(self, business_id: uuid.UUID, invoice_id: uuid.UUID) -> dict[str, Any]:
        """Charge the invoice through the provider; returns the invoice plus the payment outcome (instant with the test gateway, pending for manual)."""
        async with self.db.system_tx() as c:
            inv = await (await c.execute("SELECT * FROM invoices WHERE business_id=%s AND id=%s FOR UPDATE", (business_id, invoice_id))).fetchone()
            if inv is None:
                raise BillingError("not_found", "Invoice not found.", 404)
            if inv["status"] == "paid":
                return {**self._inv(inv), "payment": {"status": "succeeded", "mode": self.provider.mode, "instructions": None}}
            if inv["status"] != "open":
                raise BillingError("invoice_closed", "This invoice can no longer be paid.")
            biz = required(await (await c.execute("SELECT id, name FROM businesses WHERE id=%s", (business_id,))).fetchone(), "business")
            res = await self.provider.charge(dict(inv), dict(biz))
            await c.execute("INSERT INTO payments (business_id, invoice_id, provider, provider_ref, amount_paise, status) VALUES (%s,%s,%s,%s,%s,%s)",
                            (business_id, invoice_id, self.provider.name, res.provider_ref, inv["total_paise"], res.status))
            if res.status == "succeeded":
                inv = await self._settle(c, inv)
        return {**self._inv(inv), "payment": {"status": res.status, "mode": res.mode, "instructions": res.instructions}}

    async def settle_manually(self, invoice_id: uuid.UUID, reference: str | None = None) -> dict[str, Any]:
        """Platform team confirms money arrived (bank transfer / UPI) for an open invoice."""
        async with self.db.system_tx() as c:
            inv = await (await c.execute("SELECT * FROM invoices WHERE id=%s FOR UPDATE", (invoice_id,))).fetchone()
            if inv is None:
                raise BillingError("not_found", "Invoice not found.", 404)
            if inv["status"] == "paid":
                return self._inv(inv)
            if inv["status"] != "open":
                raise BillingError("invoice_closed", "This invoice can no longer be paid.")
            await c.execute("INSERT INTO payments (business_id, invoice_id, provider, provider_ref, amount_paise, status) VALUES (%s,%s,'manual',%s,%s,'succeeded')",
                            (inv["business_id"], invoice_id, reference or f"manual_{inv['number']}_{uuid.uuid4().hex[:6]}", inv["total_paise"]))
            inv = await self._settle(c, inv)
        return self._inv(inv)

    async def _settle(self, c: Any, inv: dict[str, Any]) -> dict[str, Any]:
        now = self.now()
        inv = required(await (await c.execute("UPDATE invoices SET status='paid', paid_at=%s WHERE id=%s RETURNING *", (now, inv["id"]))).fetchone(), "invoice")
        sub = await self._sub(c, inv["business_id"])
        # the invoice that buys the newest period decides the subscription state
        if sub["current_period_end"] is None or inv["period_end"] >= sub["current_period_end"]:
            await c.execute(
                """UPDATE subscriptions SET plan=%s, billing_interval=%s, status='active', current_period_start=%s, current_period_end=%s,
                          cancel_at_period_end=false, pending_plan=NULL, pending_interval=NULL, trial_ends_at=NULL WHERE business_id=%s""",
                (inv["plan"], inv["billing_interval"], inv["period_start"], inv["period_end"], inv["business_id"]))
        else:
            await c.execute("UPDATE subscriptions SET status='active' WHERE business_id=%s AND status='past_due'", (inv["business_id"],))
        await self._unpause(c, inv["business_id"])
        return inv

    async def _unpause(self, c: Any, business_id: uuid.UUID) -> None:
        b = await (await c.execute("SELECT onboarding FROM businesses WHERE id=%s", (business_id,))).fetchone()
        ob = dict((b or {}).get("onboarding") or {})
        if ob.pop("billing_paused", None):
            await c.execute("UPDATE businesses SET ai_enabled=true, onboarding=%s WHERE id=%s", (jsonb(ob), business_id))

    async def _lapse(self, c: Any, business_id: uuid.UUID, why: str) -> None:
        b = await (await c.execute("SELECT onboarding, ai_enabled FROM businesses WHERE id=%s", (business_id,))).fetchone()
        ob = dict((b or {}).get("onboarding") or {})
        if b and b["ai_enabled"]:
            ob["billing_paused"] = True
            await c.execute("UPDATE businesses SET ai_enabled=false, onboarding=%s WHERE id=%s", (jsonb(ob), business_id))
        await c.execute("INSERT INTO operator_alerts (business_id, severity, kind, message) VALUES (%s,'info','subscription_lapsed',%s)", (business_id, f"Subscription lapsed: {why}"))

    # ------------------------------------------------------------------ cancel / resume
    async def cancel(self, business_id: uuid.UUID) -> dict[str, Any]:
        async with self.db.system_tx() as c:
            sub = await self._sub(c, business_id)
            if sub["status"] == "trialing":
                await c.execute("UPDATE subscriptions SET status='canceled', trial_ends_at=%s WHERE business_id=%s", (self.now(), business_id))
                await self._lapse(c, business_id, "trial cancelled by the owner")
            elif sub["status"] in ("active", "past_due"):
                await c.execute("UPDATE subscriptions SET cancel_at_period_end=true, pending_plan=NULL, pending_interval=NULL WHERE business_id=%s", (business_id,))
            else:
                raise BillingError("nothing_to_cancel", "There is no active subscription to cancel.")
        return await self.view(business_id)

    async def resume(self, business_id: uuid.UUID) -> dict[str, Any]:
        async with self.db.system_tx() as c:
            sub = await self._sub(c, business_id)
            if not sub["cancel_at_period_end"]:
                raise BillingError("not_cancelling", "The subscription is not set to cancel.")
            await c.execute("UPDATE subscriptions SET cancel_at_period_end=false WHERE business_id=%s", (business_id,))
        return await self.view(business_id)

    # ------------------------------------------------------------------ enforcement
    async def assert_can_run(self, business_id: uuid.UUID) -> None:
        """Switching the assistant on needs a trial, an active subscription, or a pilot."""
        async with self.db.system_tx() as c:
            sub = await self._sub(c, business_id)
        if sub["status"] == "canceled" or (sub["status"] == "past_due" and sub["current_period_end"] and self.now() > sub["current_period_end"] + timedelta(days=GRACE_DAYS)):
            raise BillingError("subscription_inactive", "Your subscription has ended. Choose a plan in Billing to switch the assistant back on.", 402)

    async def assert_feature(self, business_id: uuid.UUID, feature: str) -> None:
        async with self.db.system_tx() as c:
            sub = await self._sub(c, business_id)
        plan = PLANS[sub["plan"]]
        if not getattr(plan, feature):
            raise BillingError("plan_feature", f"This is part of the Growth plan and above. You are on {plan.name}.", 402)

    async def check_limit(self, business_id: uuid.UUID, kind: str) -> None:
        async with self.db.system_tx() as c:
            sub = await self._sub(c, business_id)
        plan = PLANS[sub["plan"]]
        cap = {"products": plan.max_products, "numbers": plan.max_numbers, "team": plan.max_team}[kind]
        if cap is None:
            return
        use = await self.usage(business_id, self.now(), self.now())
        if use[kind] >= cap:
            label = {"products": "products", "numbers": "WhatsApp numbers", "team": "team members"}[kind]
            raise BillingError("plan_limit", f"Your {plan.name} plan includes up to {cap} {label}. Upgrade your plan to add more.", 402)

    # ------------------------------------------------------------------ the periodic cycle (scheduler)
    async def run_cycle(self) -> dict[str, int]:
        now = self.now()
        done = {"trial_ended": 0, "renewed": 0, "ended": 0, "past_due": 0, "lapsed": 0}
        async with self.db.system_tx() as c:
            for r in await (await c.execute("SELECT business_id FROM subscriptions WHERE status='trialing' AND trial_ends_at <= %s FOR UPDATE", (now,))).fetchall():
                await c.execute("UPDATE subscriptions SET status='canceled' WHERE business_id=%s", (r["business_id"],))
                await self._lapse(c, r["business_id"], "free trial ended")
                done["trial_ended"] += 1
            for sub in await (await c.execute("SELECT * FROM subscriptions WHERE status IN ('active','past_due') AND current_period_end <= %s FOR UPDATE", (now,))).fetchall():
                bid = sub["business_id"]
                if sub["cancel_at_period_end"]:
                    await c.execute("UPDATE subscriptions SET status='canceled' WHERE business_id=%s", (bid,))
                    await self._lapse(c, bid, "cancelled at period end")
                    done["ended"] += 1
                    continue
                await self._renew(c, sub, now)
                done["renewed"] += 1
            for inv in await (await c.execute("SELECT i.*, s.status AS sub_status FROM invoices i JOIN subscriptions s ON s.business_id=i.business_id WHERE i.status='open' AND i.due_at <= %s", (now,))).fetchall():
                if inv["sub_status"] == "active":
                    await c.execute("UPDATE subscriptions SET status='past_due' WHERE business_id=%s", (inv["business_id"],))
                    await c.execute("INSERT INTO operator_alerts (business_id, severity, kind, message) VALUES (%s,'warning','payment_overdue',%s)",
                                    (inv["business_id"], f"Invoice {inv['number']} is overdue"))
                    done["past_due"] += 1
                if inv["due_at"] + timedelta(days=GRACE_DAYS) <= now:
                    sub = await self._sub(c, inv["business_id"])
                    b = await (await c.execute("SELECT onboarding FROM businesses WHERE id=%s", (inv["business_id"],))).fetchone()
                    if not (b and (b["onboarding"] or {}).get("billing_paused")):
                        await self._lapse(c, inv["business_id"], f"invoice {inv['number']} unpaid")
                        done["lapsed"] += 1
        return done

    async def _renew(self, c: Any, sub: dict[str, Any], now: datetime) -> None:
        bid = sub["business_id"]
        plan_code = sub["pending_plan"] or sub["plan"]
        interval = sub["pending_interval"] or sub["billing_interval"]
        plan = PLANS[plan_code]
        start = sub["current_period_end"]
        end = period_end(start, interval)
        old = PLANS[sub["plan"]]
        lines = [{"description": f"{plan.name} plan, {'yearly' if interval == 'year' else 'monthly'}", "quantity": 1, "unit_paise": price_for(plan, interval), "amount_paise": price_for(plan, interval)}]
        if old.included_conversations is not None:
            used = (await (await c.execute(
                "SELECT count(DISTINCT conversation_id) AS n FROM turns WHERE business_id=%s AND created_at >= %s AND created_at < %s AND status IN ('planned','sent')",
                (bid, sub["current_period_start"], sub["current_period_end"]))).fetchone())["n"]
            extra = max(0, used - old.included_conversations)
            if extra and old.overage_paise:
                lines.append({"description": f"{extra} extra AI conversations beyond {old.included_conversations} included", "quantity": extra,
                              "unit_paise": old.overage_paise, "amount_paise": extra * old.overage_paise})
        dup = await (await c.execute("SELECT 1 FROM invoices WHERE business_id=%s AND period_start=%s AND status <> 'void'", (bid, start))).fetchone()
        if not dup:
            await self._create_invoice(c, bid, plan_code, interval, start, end, lines)
        await c.execute("UPDATE subscriptions SET plan=%s, billing_interval=%s, current_period_start=%s, current_period_end=%s, pending_plan=NULL, pending_interval=NULL WHERE business_id=%s",
                        (plan_code, interval, start, end, bid))

    # ------------------------------------------------------------------ platform view (operator console)
    async def platform_summary(self) -> dict[str, Any]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute("SELECT plan, status, billing_interval FROM subscriptions")).fetchall()
            paid = (await (await c.execute("SELECT COALESCE(sum(total_paise),0) AS s FROM invoices WHERE status='paid' AND paid_at >= date_trunc('month', now())")).fetchone())["s"]
            open_ = (await (await c.execute("SELECT COALESCE(sum(total_paise),0) AS s, count(*) AS n FROM invoices WHERE status='open'")).fetchone())
        by: dict[str, int] = {}
        mrr = 0
        for r in rows:
            by[r["status"]] = by.get(r["status"], 0) + 1
            if r["status"] in ("active", "past_due"):
                p = PLANS[r["plan"]]
                mrr += p.price_year_paise // 12 if r["billing_interval"] == "year" else p.price_month_paise
        return {"subscriptions_by_status": by, "mrr_paise": mrr, "collected_this_month_paise": int(paid), "outstanding_paise": int(open_["s"]), "open_invoices": int(open_["n"])}

    # ------------------------------------------------------------------ printable invoice
    async def invoice_html(self, business_id: uuid.UUID, invoice_id: uuid.UUID) -> str:
        inv = await self.invoice(business_id, invoice_id)
        async with self.db.system_tx() as c:
            b = required(await (await c.execute("SELECT name, profile FROM businesses WHERE id=%s", (business_id,))).fetchone(), "business")
        e = html.escape

        def rupees(p: int) -> str:
            return f"₹{p / 100:,.2f}"

        def d(t: datetime) -> str:
            return t.strftime("%d %b %Y")

        rows = "".join(f"<tr><td>{e(x['description'])}</td><td class=n>{x['quantity']}</td><td class=n>{rupees(x['unit_paise'])}</td><td class=n>{rupees(x['amount_paise'])}</td></tr>" for x in inv["lines"])
        return f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Invoice {e(inv['number'])}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 20px;color:#111}}h1{{font-size:24px;margin:0}}
table{{width:100%;border-collapse:collapse;margin-top:24px}}th,td{{padding:10px 8px;border-bottom:1px solid #ddd;text-align:left}}td.n,th.n{{text-align:right}}
.tot td{{border:0}}.muted{{color:#666}}.badge{{display:inline-block;padding:2px 10px;border-radius:99px;background:{'#d1fae5' if inv['status']=='paid' else '#fef3c7'}}}
@media print{{body{{margin:0}}}}</style>
<h1>Tax invoice <span class=badge>{e(inv['status'].upper())}</span></h1>
<p class=muted>{e(inv['number'])} · issued {d(inv['issued_at'])} · due {d(inv['due_at'])}</p>
<p><b>{e(self.seller['name'])}</b><br>{e(self.seller['address'])}<br>GSTIN {e(self.seller['gstin'])}</p>
<p><b>Billed to</b><br>{e(b['name'])}<br>{e((b['profile'] or {}).get('address') or '')}</p>
<table><tr><th>Description</th><th class=n>Qty</th><th class=n>Rate</th><th class=n>Amount</th></tr>{rows}
<tr class=tot><td></td><td></td><td class=n>Subtotal</td><td class=n>{rupees(inv['subtotal_paise'])}</td></tr>
<tr class=tot><td></td><td></td><td class=n>GST (18%)</td><td class=n>{rupees(inv['gst_paise'])}</td></tr>
<tr class=tot><td></td><td></td><td class=n><b>Total</b></td><td class=n><b>{rupees(inv['total_paise'])}</b></td></tr></table>
<p class=muted>Service period {d(inv['period_start'])} to {d(inv['period_end'])}. {'Paid on ' + d(inv['paid_at']) + '.' if inv['paid_at'] else ''}</p></html>"""

