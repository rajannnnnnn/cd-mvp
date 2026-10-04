from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from salesai.api.deps import RT, Tenant, tx

router = APIRouter(tags=["metrics"])


class DayPoint(BaseModel):
    date: str
    conversations: int
    customer_messages: int
    ai_replies: int
    deals: int


class Overview(BaseModel):
    today: dict[str, int]
    series: list[DayPoint]
    pipeline: dict[str, int]
    ai: dict[str, Any]


@router.get("/metrics/overview", response_model=Overview, summary="Dashboard numbers (FR-RP-2)")
async def overview(rt: RT, p: Tenant, days: Annotated[int, Query(ge=1, le=60)] = 14) -> Any:
    async with tx(rt, p) as c:
        biz = await (await c.execute("SELECT timezone FROM businesses WHERE id=%s", (p.business_id,))).fetchone()
        tz = biz["timezone"]
        today = await (await c.execute(
            """SELECT
                 (SELECT count(*) FROM conversations WHERE last_inbound_at >= date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s) AS conversations,
                 (SELECT count(*) FROM customers WHERE first_seen_at >= date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s) AS new_customers,
                 (SELECT count(*) FROM conversations WHERE lead_stage IN ('interested','negotiating','ready_to_buy') AND updated_at >= date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s) AS interested,
                 (SELECT count(*) FROM deals WHERE created_at >= date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s) AS commitments,
                 (SELECT count(*) FROM deals WHERE status='pending') AS pending_deals,
                 (SELECT count(*) FROM handoffs WHERE reason='complaint' AND created_at >= date_trunc('day', now() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s) AS complaints,
                 (SELECT count(*) FROM handoffs WHERE status='open') AS open_handoffs,
                 (SELECT count(*) FROM knowledge_gaps WHERE status='open') AS open_gaps""", {"tz": tz})).fetchone()
        series = await (await c.execute(
            """SELECT to_char(d::date, 'YYYY-MM-DD') AS date,
                 (SELECT count(DISTINCT m.conversation_id) FROM messages m WHERE m.direction='in' AND (m.created_at AT TIME ZONE %(tz)s)::date = d::date) AS conversations,
                 (SELECT count(*) FROM messages m WHERE m.direction='in' AND (m.created_at AT TIME ZONE %(tz)s)::date = d::date) AS customer_messages,
                 (SELECT count(*) FROM messages m WHERE m.sender='ai' AND m.kind='text' AND m.status <> 'cancelled' AND (m.created_at AT TIME ZONE %(tz)s)::date = d::date) AS ai_replies,
                 (SELECT count(*) FROM deals x WHERE (x.created_at AT TIME ZONE %(tz)s)::date = d::date) AS deals
               FROM generate_series((now() AT TIME ZONE %(tz)s)::date - (%(n)s - 1), (now() AT TIME ZONE %(tz)s)::date, interval '1 day') d ORDER BY d""", {"tz": tz, "n": days})).fetchall()
        pipe = await (await c.execute("SELECT lead_stage, count(*) AS n FROM conversations GROUP BY lead_stage")).fetchall()
        ai = await (await c.execute(
            """SELECT count(*) AS turns, COALESCE(avg(latency_ms),0)::int AS avg_latency_ms, COALESCE(sum(cost_micros),0) AS cost_micros,
                      count(DISTINCT conversation_id) AS conversations,
                      count(DISTINCT conversation_id) FILTER (WHERE EXISTS (SELECT 1 FROM handoffs h WHERE h.conversation_id = turns.conversation_id)) AS handed_off
               FROM turns WHERE created_at > now() - interval '30 days'""")).fetchone()
    convs = int(ai["conversations"])
    return Overview(
        today={k: int(v) for k, v in today.items()}, series=[DayPoint(**{k: (v if k == "date" else int(v)) for k, v in r.items()}) for r in series],
        pipeline={r["lead_stage"]: int(r["n"]) for r in pipe},
        ai={"turns_30d": int(ai["turns"]), "avg_latency_ms": ai["avg_latency_ms"], "cost_inr_30d": round(int(ai["cost_micros"]) / 1_000_000, 2),
            "autonomous_pct": round(100 * (convs - int(ai["handed_off"])) / convs) if convs else None})
