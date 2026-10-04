"""Business analytics and reports for the owner: KPIs with the previous period for comparison, trends, funnel, hand-off reasons,
what people ask about, busiest hours, and CSV exports. Everything is computed from the shop's own rows (row-level security applies);
no floor price or floor-derived figure appears here (INV-1)."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel

from salesai.api.deps import RT, Tenant, tx
from salesai.api.errors import ApiError
from salesai.db import Conn

router = APIRouter(tags=["analytics"])


class Kpis(BaseModel):
    conversations: int
    new_customers: int
    customer_messages: int
    ai_replies: int
    deals: int
    deal_value: float
    handoffs: int
    handoff_rate: float          # share of conversations that needed the owner
    conversion_rate: float       # share of conversations that ended in an order or visit
    avg_first_reply_s: float | None
    messages_per_conversation: float


class Point(BaseModel):
    date: str
    conversations: int
    ai_replies: int
    deals: int


class Count(BaseModel):
    label: str
    n: int


class KindRow(BaseModel):
    kind: str
    n: int
    confirmed: int
    value: float


class HourRow(BaseModel):
    hour: int
    n: int


class AnalyticsOut(BaseModel):
    start: date
    end: date
    days: int
    kpis: Kpis
    previous: Kpis
    series: list[Point]
    funnel: list[Count]
    handoff_reasons: list[Count]
    deals_by_kind: list[KindRow]
    busiest_hours: list[HourRow]
    top_products: list[Count]
    ai: dict[str, Any]


async def _kpis(c: Conn, tz: str, start: datetime, end: datetime) -> dict[str, Any]:
    r = await (await c.execute(
        """WITH win AS (SELECT %(s)s::timestamptz AS s, %(e)s::timestamptz AS e),
           convs AS (SELECT DISTINCT conversation_id FROM messages, win WHERE direction='in' AND created_at >= win.s AND created_at < win.e),
           first_reply AS (
             SELECT EXTRACT(EPOCH FROM (o.sent_at - i.created_at)) AS secs
             FROM messages i, win JOIN LATERAL (SELECT sent_at FROM messages o WHERE o.conversation_id=i.conversation_id AND o.direction='out' AND o.sender='ai' AND o.sent_at > i.created_at ORDER BY o.sent_at LIMIT 1) o ON true
             WHERE i.direction='in' AND i.created_at >= win.s AND i.created_at < win.e AND i.id = (SELECT id FROM messages x WHERE x.conversation_id=i.conversation_id AND x.direction='in' AND x.created_at >= win.s ORDER BY x.created_at LIMIT 1))
           SELECT
             (SELECT count(*) FROM convs) AS conversations,
             (SELECT count(*) FROM customers, win WHERE first_seen_at >= win.s AND first_seen_at < win.e) AS new_customers,
             (SELECT count(*) FROM messages, win WHERE direction='in' AND created_at >= win.s AND created_at < win.e) AS customer_messages,
             (SELECT count(*) FROM messages, win WHERE sender='ai' AND kind='text' AND status <> 'cancelled' AND created_at >= win.s AND created_at < win.e) AS ai_replies,
             (SELECT count(*) FROM deals, win WHERE created_at >= win.s AND created_at < win.e) AS deals,
             (SELECT COALESCE(sum(value),0) FROM deals, win WHERE status <> 'lost' AND created_at >= win.s AND created_at < win.e) AS deal_value,
             (SELECT count(DISTINCT conversation_id) FROM handoffs, win WHERE created_at >= win.s AND created_at < win.e) AS handoffs,
             (SELECT count(DISTINCT conversation_id) FROM deals, win WHERE created_at >= win.s AND created_at < win.e) AS converted,
             (SELECT avg(secs) FROM first_reply) AS avg_first_reply_s""", {"s": start, "e": end})).fetchone()
    convs = int(r["conversations"])
    return {"conversations": convs, "new_customers": int(r["new_customers"]), "customer_messages": int(r["customer_messages"]), "ai_replies": int(r["ai_replies"]),
            "deals": int(r["deals"]), "deal_value": float(r["deal_value"]), "handoffs": int(r["handoffs"]),
            "handoff_rate": round(int(r["handoffs"]) / convs, 3) if convs else 0.0,
            "conversion_rate": round(int(r["converted"]) / convs, 3) if convs else 0.0,
            "avg_first_reply_s": round(float(r["avg_first_reply_s"]), 1) if r["avg_first_reply_s"] is not None else None,
            "messages_per_conversation": round(int(r["customer_messages"]) / convs, 1) if convs else 0.0}


async def compute(c: Conn, tz: str, first: date, last: date) -> dict[str, Any]:
    """Local calendar days first..last inclusive, in the shop's timezone."""
    days = (last - first).days + 1
    s = (await (await c.execute("SELECT (%s::date::timestamp AT TIME ZONE %s) AS t", (first, tz))).fetchone())["t"]
    e = (await (await c.execute("SELECT ((%s::date + 1)::timestamp AT TIME ZONE %s) AS t", (last, tz))).fetchone())["t"]
    ps = (await (await c.execute("SELECT ((%s::date - %s)::timestamp AT TIME ZONE %s) AS t", (first, days, tz))).fetchone())["t"]
    k, pk = await _kpis(c, tz, s, e), await _kpis(c, tz, ps, s)
    series = await (await c.execute(
        """SELECT to_char(d::date,'YYYY-MM-DD') AS date,
             (SELECT count(DISTINCT m.conversation_id) FROM messages m WHERE m.direction='in' AND (m.created_at AT TIME ZONE %(tz)s)::date = d::date) AS conversations,
             (SELECT count(*) FROM messages m WHERE m.sender='ai' AND m.kind='text' AND m.status <> 'cancelled' AND (m.created_at AT TIME ZONE %(tz)s)::date = d::date) AS ai_replies,
             (SELECT count(*) FROM deals x WHERE (x.created_at AT TIME ZONE %(tz)s)::date = d::date) AS deals
           FROM generate_series(%(a)s::date, %(b)s::date, interval '1 day') d ORDER BY d""", {"tz": tz, "a": first, "b": last})).fetchall()
    funnel = await (await c.execute(
        "SELECT lead_stage AS label, count(*) AS n FROM conversations WHERE last_inbound_at >= %s AND last_inbound_at < %s GROUP BY lead_stage", (s, e))).fetchall()
    order = {"new": 0, "exploring": 1, "interested": 2, "negotiating": 3, "ready_to_buy": 4, "won": 5, "lost": 6}
    funnel = sorted(funnel, key=lambda x: order.get(x["label"], 9))
    reasons = await (await c.execute("SELECT reason AS label, count(*) AS n FROM handoffs WHERE created_at >= %s AND created_at < %s GROUP BY reason ORDER BY n DESC", (s, e))).fetchall()
    kinds = await (await c.execute(
        """SELECT kind, count(*) AS n, count(*) FILTER (WHERE status='won') AS confirmed, COALESCE(sum(value) FILTER (WHERE status <> 'lost'),0) AS value
           FROM deals WHERE created_at >= %s AND created_at < %s GROUP BY kind ORDER BY n DESC""", (s, e))).fetchall()
    hours = await (await c.execute(
        """SELECT h AS hour, count(m.id) AS n FROM generate_series(0,23) h
           LEFT JOIN messages m ON m.direction='in' AND m.created_at >= %(s)s AND m.created_at < %(e)s AND EXTRACT(HOUR FROM m.created_at AT TIME ZONE %(tz)s) = h
           GROUP BY h ORDER BY h""", {"s": s, "e": e, "tz": tz})).fetchall()
    products = await (await c.execute(
        """SELECT p.name AS label, count(*) AS n FROM conversations cv
           JOIN product_variants v ON v.business_id = cv.business_id AND v.id = NULLIF(cv.qualification->>'focus_variant_id','')::uuid
           JOIN products p ON p.business_id = v.business_id AND p.id = v.product_id
           WHERE cv.last_inbound_at >= %s AND cv.last_inbound_at < %s GROUP BY p.name ORDER BY n DESC LIMIT 8""", (s, e))).fetchall()
    ai = await (await c.execute("SELECT count(*) AS turns, avg(latency_ms) AS avg_latency_ms FROM turns WHERE created_at >= %s AND created_at < %s AND status IN ('planned','sent')", (s, e))).fetchone()
    return {"start": first, "end": last, "days": days, "kpis": k, "previous": pk, "series": series, "funnel": funnel, "handoff_reasons": reasons,
            "deals_by_kind": [{**x, "value": float(x["value"])} for x in kinds], "busiest_hours": hours, "top_products": products,
            "ai": {"turns": int(ai["turns"]), "avg_latency_ms": int(ai["avg_latency_ms"]) if ai["avg_latency_ms"] is not None else None}}


async def _range(c: Conn, p_bid: Any, start: date | None, end: date | None, days: int) -> tuple[str, date, date]:
    tz = (await (await c.execute("SELECT timezone FROM businesses WHERE id=%s", (p_bid,))).fetchone())["timezone"]
    today = (await (await c.execute("SELECT (now() AT TIME ZONE %s)::date AS d", (tz,))).fetchone())["d"]
    if start and end:
        if end < start or (end - start).days > 365:
            raise ApiError(422, "invalid_range", "Choose a range of at most one year, ending after it starts.")
        return tz, start, end
    return tz, today - timedelta(days=days - 1), today


@router.get("/analytics", response_model=AnalyticsOut, summary="Business analytics for the last N days, with the previous period for comparison")
async def analytics(rt: RT, p: Tenant, days: Annotated[int, Query(ge=1, le=90)] = 30) -> Any:
    async with tx(rt, p) as c:
        tz, a, b = await _range(c, p.bid, None, None, days)
        return await compute(c, tz, a, b)


@router.get("/reports/summary", response_model=AnalyticsOut, summary="The same figures for an explicit date range (a report)")
async def report(rt: RT, p: Tenant, start: date, end: date) -> Any:
    async with tx(rt, p) as c:
        tz, a, b = await _range(c, p.bid, start, end, 30)
        return await compute(c, tz, a, b)


EXPORTS: dict[str, tuple[str, str]] = {
    "conversations": ("""SELECT cu.name AS customer, cu.wa_id AS phone, cv.lead_stage AS stage, cv.lost_reason, cv.summary,
                                to_char(cv.created_at,'YYYY-MM-DD HH24:MI') AS started, to_char(cv.last_inbound_at,'YYYY-MM-DD HH24:MI') AS last_customer_message
                         FROM conversations cv JOIN customers cu ON cu.business_id = cv.business_id AND cu.id = cv.customer_id
                         WHERE cv.last_inbound_at >= %s AND cv.last_inbound_at < %s ORDER BY cv.last_inbound_at""", "conversations"),
    "deals": ("""SELECT to_char(d.created_at,'YYYY-MM-DD HH24:MI') AS created, d.kind, d.status, d.value, cu.name AS customer, cu.wa_id AS phone, d.items::text AS items
                 FROM deals d JOIN conversations cv ON cv.business_id = d.business_id AND cv.id = d.conversation_id
                 JOIN customers cu ON cu.business_id = cv.business_id AND cu.id = cv.customer_id
                 WHERE d.created_at >= %s AND d.created_at < %s ORDER BY d.created_at""", "orders-and-visits"),
    "customers": ("""SELECT name, wa_id AS phone, to_char(first_seen_at,'YYYY-MM-DD') AS first_seen, to_char(last_seen_at,'YYYY-MM-DD') AS last_seen,
                            is_personal, opted_out FROM customers WHERE last_seen_at >= %s AND last_seen_at < %s ORDER BY last_seen_at DESC""", "customers"),
}


def _safe(v: Any) -> Any:
    """Spreadsheet formula injection guard: text a customer controls must not start a formula."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@router.get("/reports/export/{kind}.csv", summary="Download conversations, orders and visits, or customers as CSV")
async def export_csv(kind: Literal["conversations", "deals", "customers"], rt: RT, p: Tenant, start: date, end: date) -> Response:
    async with tx(rt, p) as c:
        tz, a, b = await _range(c, p.bid, start, end, 30)
        s = (await (await c.execute("SELECT (%s::date::timestamp AT TIME ZONE %s) AS t", (a, tz))).fetchone())["t"]
        e = (await (await c.execute("SELECT ((%s::date + 1)::timestamp AT TIME ZONE %s) AS t", (b, tz))).fetchone())["t"]
        sql, label = EXPORTS[kind]
        rows = await (await c.execute(sql, (s, e))).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([_safe(v) for v in r.values()])
    else:
        w.writerow(["no rows in this period"])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{label}-{a}-to-{b}.csv"'})
