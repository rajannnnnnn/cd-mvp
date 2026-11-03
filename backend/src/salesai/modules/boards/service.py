"""The founder's four boards: Sales outcome, Growth, Money, Health. Aggregates only, computed on the platform role from counts and sums:
nothing here selects a customer's words, name or number, an order's details, or a floor price (a test scans every board for them).

Figures the platform does not record are returned as `null` with the reason in `not_measured`, never as a reassuring zero:
a dashboard that shows 0 for something nobody measures is worse than one that says so."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from salesai.config import ALL_QUEUES
from salesai.db import Database
from salesai.modules.billing import PLANS, MetaUsage
from salesai.queue.base import Queue

HANDOFF_REASONS = ("unknown_answer", "on_request_price", "below_floor", "customer_asked_human", "complaint", "high_value", "system_failure", "other")
CHECKS = ("price", "urgency", "human_claim", "identity", "internal", "sensitive")
SEVERITIES = ("low", "medium", "high", "critical")
SUB_STATES = ("trialing", "active", "past_due", "canceled", "pilot")

_ATTEMPT_FAILURES = """jsonb_array_elements(CASE WHEN jsonb_typeof(t.decision->'checks'->'attempts') = 'array'
                                                 THEN t.decision->'checks'->'attempts' ELSE '[]'::jsonb END) a,
                       jsonb_array_elements_text(COALESCE(a->'failures', '[]'::jsonb)) f"""


def _pct(part: float, whole: float) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _one(row: dict[str, Any] | None) -> dict[str, Any]:
    return row or {}


class Boards:
    def __init__(self, db: Database, queue: Queue, meta_usage: MetaUsage):
        self.db, self.queue, self.meta = db, queue, meta_usage

    # ------------------------------------------------------------------ sales outcome
    async def sales(self, days: int) -> dict[str, Any]:
        async with self.db.system_tx() as c:
            r = _one(await (await c.execute(
                """WITH since AS (SELECT now() - make_interval(days => %s) AS t),
                        chats AS (SELECT DISTINCT m.business_id, m.conversation_id AS id FROM messages m, since
                                   WHERE m.direction = 'in' AND m.created_at >= since.t)
                   SELECT (SELECT count(*) FROM chats) AS conversations,
                          (SELECT count(*) FROM chats, since WHERE
                              NOT EXISTS (SELECT 1 FROM handoffs h WHERE h.conversation_id = chats.id AND h.created_at >= since.t)
                              AND NOT EXISTS (SELECT 1 FROM messages o WHERE o.conversation_id = chats.id AND o.sender = 'owner' AND o.created_at >= since.t)) AS autonomous,
                          (SELECT count(*) FROM chats, since WHERE
                              EXISTS (SELECT 1 FROM deals d WHERE d.conversation_id = chats.id AND d.created_at >= since.t)) AS commitments""",
                (days,))).fetchone())
            wins = _one(await (await c.execute(
                """SELECT count(*) AS n, COALESCE(sum(value), 0) AS v FROM deals
                    WHERE status = 'won' AND COALESCE(closed_at, created_at) >= now() - make_interval(days => %s)""", (days,))).fetchone())
            handoffs = {x["reason"]: int(x["n"]) for x in await (await c.execute(
                "SELECT reason, count(*) AS n FROM handoffs WHERE created_at >= now() - make_interval(days => %s) GROUP BY reason", (days,))).fetchall()}
            turns = int(_one(await (await c.execute("SELECT count(*) AS n FROM turns WHERE created_at >= now() - make_interval(days => %s)", (days,))).fetchone())["n"])
            caught = {x["check"]: int(x["n"]) for x in await (await c.execute(
                f"""SELECT split_part(f, ':', 1) AS check, count(DISTINCT t.id) AS n FROM turns t, {_ATTEMPT_FAILURES}
                     WHERE t.created_at >= now() - make_interval(days => %s) GROUP BY 1""", (days,))).fetchall()}  # noqa: S608 (constant fragment)
            shops = await (await c.execute(
                """WITH since AS (SELECT now() - make_interval(days => %s) AS t)
                   SELECT b.id, b.name,
                          (SELECT count(DISTINCT m.conversation_id) FROM messages m, since WHERE m.business_id = b.id AND m.direction = 'in' AND m.created_at >= since.t) AS conversations,
                          (SELECT count(*) FROM deals d, since WHERE d.business_id = b.id AND d.status = 'won' AND COALESCE(d.closed_at, d.created_at) >= since.t) AS wins,
                          (SELECT count(*) FROM handoffs h, since WHERE h.business_id = b.id AND h.created_at >= since.t) AS handoffs
                     FROM businesses b ORDER BY conversations DESC, b.name LIMIT 25""", (days,))).fetchall()
        conversations, autonomous, commitments = int(r.get("conversations", 0)), int(r.get("autonomous", 0)), int(r.get("commitments", 0))
        return {
            "days": days, "conversations": conversations, "autonomous": autonomous, "autonomous_pct": _pct(autonomous, conversations),
            "commitments": commitments, "commitment_pct": _pct(commitments, conversations),
            "wins": {"count": int(wins.get("n", 0)), "value_inr": float(wins.get("v", 0))},
            "handoffs": {k: handoffs.get(k, 0) for k in HANDOFF_REASONS},
            "turns": turns, "caught": {k: caught.get(k, 0) for k in CHECKS},
            "by_shop": [{"business_id": str(s["id"]), "name": s["name"], "conversations": int(s["conversations"]), "wins": int(s["wins"]),
                         "handoffs": int(s["handoffs"])} for s in shops],
            "not_measured": {
                "reached_customers_unauthorized_price": "Prices are checked against the pricing engine before sending; 'caught' counts what the checks stopped. "
                                                        "How many reached a customer is measured by the evaluation suite, not recorded in production yet.",
                "bot_suspicion_rate": "Not recorded yet: the agent does not flag customers who suspect they are talking to a bot.",
                "wrong_fact_reports": "Not recorded yet: there is no way for an owner to flag a wrong answer.",
            },
        }

    # ------------------------------------------------------------------ growth
    async def growth(self, days: int) -> dict[str, Any]:
        async with self.db.system_tx() as c:
            f = _one(await (await c.execute(
                """WITH cohort AS (SELECT id FROM businesses WHERE created_at >= now() - make_interval(days => %s))
                   SELECT (SELECT count(*) FROM cohort) AS signed_up,
                          (SELECT count(*) FROM cohort WHERE EXISTS (SELECT 1 FROM whatsapp_numbers n WHERE n.business_id = cohort.id)) AS number_connected,
                          (SELECT count(*) FROM cohort WHERE EXISTS (SELECT 1 FROM messages m WHERE m.business_id = cohort.id AND m.sender = 'ai' AND m.direction = 'out' AND m.status <> 'failed')) AS first_ai_reply,
                          (SELECT count(*) FROM cohort WHERE EXISTS (SELECT 1 FROM deals d WHERE d.business_id = cohort.id)) AS first_commitment,
                          (SELECT count(*) FROM cohort WHERE EXISTS (SELECT 1 FROM subscriptions s WHERE s.business_id = cohort.id AND s.status IN ('active', 'past_due'))
                                                           OR EXISTS (SELECT 1 FROM invoices i WHERE i.business_id = cohort.id AND i.status = 'paid')) AS paid""", (days,))).fetchone())
            subs = {x["status"]: int(x["n"]) for x in await (await c.execute("SELECT status, count(*) AS n FROM subscriptions GROUP BY status")).fetchall()}
            trial = _one(await (await c.execute(
                """SELECT count(*) AS ended, count(*) FILTER (WHERE status IN ('active', 'past_due')) AS converted FROM subscriptions
                    WHERE trial_ends_at IS NOT NULL AND trial_ends_at < now() AND trial_ends_at >= now() - make_interval(days => %s)""", (days,))).fetchone())
            churned = int(_one(await (await c.execute(
                "SELECT count(*) AS n FROM subscriptions WHERE status = 'canceled' AND updated_at >= now() - make_interval(days => %s)", (days,))).fetchone())["n"])
            tix = _one(await (await c.execute(
                """SELECT count(*) FILTER (WHERE created_at >= now() - make_interval(days => %s)) AS opened,
                          count(*) FILTER (WHERE status <> 'resolved') AS open_now FROM support_tickets""", (days,))).fetchone())
            weeks = await (await c.execute(
                """SELECT to_char(date_trunc('week', created_at), 'YYYY-MM-DD') AS week, count(*) AS n FROM businesses
                    WHERE created_at >= date_trunc('week', now()) - interval '7 weeks' GROUP BY 1 ORDER BY 1""")).fetchall()
        funnel = {k: int(f.get(k, 0)) for k in ("signed_up", "number_connected", "first_ai_reply", "first_commitment", "paid")}
        return {
            "days": days, "funnel": funnel,
            "funnel_pct": {k: _pct(v, funnel["signed_up"]) for k, v in funnel.items()},
            "subscriptions": {k: subs.get(k, 0) for k in SUB_STATES},
            "trial_to_paid": {"ended": int(trial.get("ended", 0)), "converted": int(trial.get("converted", 0)),
                              "pct": _pct(int(trial.get("converted", 0)), int(trial.get("ended", 0)))},
            "churned_in_window": churned,
            "tickets_opened": int(tix.get("opened", 0)), "tickets_open_now": int(tix.get("open_now", 0)),
            "signups_by_week": [{"week": w["week"], "signups": int(w["n"])} for w in weeks],
        }

    # ------------------------------------------------------------------ money
    async def money(self, days: int) -> dict[str, Any]:
        now = datetime.now(UTC)
        async with self.db.system_tx() as c:
            subs = await (await c.execute(
                """SELECT s.business_id, b.name, s.plan, s.billing_interval FROM subscriptions s JOIN businesses b ON b.id = s.business_id
                    WHERE s.status IN ('active', 'past_due')""")).fetchall()
            costs = {x["business_id"]: int(x["micros"]) for x in await (await c.execute(
                """SELECT business_id, COALESCE(sum(cost_micros), 0) AS micros FROM turns
                    WHERE created_at >= now() - make_interval(days => %s) GROUP BY business_id""", (days,))).fetchall()}
            names = {x["id"]: x["name"] for x in await (await c.execute("SELECT id, name FROM businesses")).fetchall()}
        by_shop: list[dict[str, Any]] = []
        mrr = 0.0
        shop_ids = {s["business_id"] for s in subs} | set(costs)
        paying = {s["business_id"]: s for s in subs}
        for bid in shop_ids:
            s = paying.get(bid)
            monthly = 0.0
            if s:
                plan = PLANS[s["plan"]]
                monthly = (plan.price_year_paise / 12 if s["billing_interval"] == "year" else plan.price_month_paise) / 100
            mrr += monthly
            usage = await self.meta.month(bid, now.year, now.month)
            channel = float(usage["estimated_cost_inr"]["platform_pays"])           # what the platform pays Meta; shops on their own account pay Meta directly
            model = costs.get(bid, 0) / 1e6
            revenue = monthly * days / 30
            by_shop.append({"business_id": str(bid), "name": names.get(bid, "?"), "plan": s["plan"] if s else None, "revenue_inr": round(revenue, 4),
                            "model_cost_inr": round(model, 4), "channel_cost_inr": round(channel, 4),
                            "margin_inr": round(revenue - model - channel, 4),
                            "margin_pct": _pct(revenue - model - channel, revenue)})
        by_shop.sort(key=lambda r: r["margin_inr"])                                  # the shops losing money first
        revenue_total = sum(r["revenue_inr"] for r in by_shop)
        model_total, channel_total = sum(r["model_cost_inr"] for r in by_shop), sum(r["channel_cost_inr"] for r in by_shop)
        return {
            "days": days, "mrr_inr": round(mrr, 2), "paying_shops": len(paying), "arpu_inr": round(mrr / len(paying), 2) if paying else None,
            "cost": {"model_inr": round(model_total, 4), "channel_inr": round(channel_total, 4)},
            "gross_margin_pct": _pct(revenue_total - model_total - channel_total, revenue_total),
            "by_shop": by_shop[:100],
            "notes": {"channel_cost": "This month's estimate of what the platform pays Meta, for numbers on the platform's own account only. Meta's invoice is the source of truth.",
                      "revenue": "Plan price, prorated to the window."},
        }

    # ------------------------------------------------------------------ health
    async def health(self, days: int) -> dict[str, Any]:
        stats = await self.queue.stats(list(ALL_QUEUES))
        async with self.db.system_tx() as c:
            alerts = {x["severity"]: int(x["n"]) for x in await (await c.execute(
                "SELECT severity, count(*) AS n FROM operator_alerts WHERE resolved_at IS NULL GROUP BY severity")).fetchall()}
            numbers = await (await c.execute(
                """SELECT b.name AS business_name, n.display_phone, n.status, n.quality_rating AS quality FROM whatsapp_numbers n JOIN businesses b ON b.id = n.business_id
                    WHERE n.status <> 'connected' OR n.quality_rating IN ('yellow', 'red') ORDER BY n.quality_rating NULLS LAST, b.name LIMIT 100""")).fetchall()
            totals = _one(await (await c.execute(
                "SELECT count(*) AS n, count(*) FILTER (WHERE status = 'connected') AS connected FROM whatsapp_numbers")).fetchone())
            llm = _one(await (await c.execute(
                """SELECT COALESCE(sum((decision->'llm'->>'calls')::int), 0) AS calls, COALESCE(sum((decision->'llm'->>'failed')::int), 0) AS failed
                     FROM turns WHERE created_at >= now() - make_interval(days => %s)""", (days,))).fetchone())
            sends = _one(await (await c.execute(
                """SELECT count(*) AS total, count(*) FILTER (WHERE status = 'failed') AS failed FROM messages
                    WHERE direction = 'out' AND created_at >= now() - make_interval(days => %s)""", (days,))).fetchone())
            hook = _one(await (await c.execute(
                """SELECT count(*) AS n, percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM processed_at - received_at)) * 1000 AS p99
                     FROM webhook_events WHERE processed_at IS NOT NULL AND received_at >= now() - make_interval(days => %s)""", (days,))).fetchone())
        calls, failed = int(llm.get("calls", 0)), int(llm.get("failed", 0))
        total, bad = int(sends.get("total", 0)), int(sends.get("failed", 0))
        return {
            "days": days,
            "queues": [{"queue": q.queue, "pending": q.pending, "due": q.due, "oldest_age_s": round(q.oldest_due_age_s, 1), "dead": q.dead} for q in stats],
            "dead_letters": sum(q.dead for q in stats),
            "alerts": {k: alerts.get(k, 0) for k in SEVERITIES},
            "numbers": {"total": int(totals.get("n", 0)), "connected": int(totals.get("connected", 0))},
            "numbers_needing_attention": [dict(n) for n in numbers],
            "llm": {"calls": calls, "failed": failed, "error_pct": _pct(failed, calls)},
            "sends": {"total": total, "failed": bad, "failed_pct": _pct(bad, total)},
            "webhook_processing_p99_ms": round(float(hook["p99"]), 1) if hook.get("p99") is not None else None,
            "not_measured": {
                "webhook_p99_ms": "Time to acknowledge Meta's webhook is measured in the metrics endpoint, not stored; the processing time above is stored.",
                "llm_fallback_rate": "Not recorded per turn yet.",
                "eval_pass_rate": "The evaluation suite runs in CI and on demand; its result is not stored in the platform.",
            },
        }
