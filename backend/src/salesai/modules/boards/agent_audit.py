"""The agent audit as the founder reads it (ADR 0037): every time the AI was woken, what woke it, how it routed, what its model calls cost
in total, how long the customer waited, and how it ended, with "no reply" and its reason counted like any other outcome. Names, counts,
milliseconds and money only: no customer words, name or number, no price and no floor."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from salesai.db import Database

OUTCOMES = ("replied", "reaction_only", "no_reply", "blocked", "superseded", "failed")
TRIGGERS = ("turn_detection", "scheduler_nudge")
PREMATURE_WINDOW_S = 20          # the customer wrote again within this long of the AI starting: it did not wait for them to finish

_TIMING = """
    LEFT JOIN LATERAL (SELECT min(m.sent_at) AS first_sent, max(m.sent_at) AS last_sent, count(*) AS parts FROM messages m
                        WHERE m.turn_id = r.turn_id AND m.business_id = r.business_id AND m.direction = 'out' AND m.sent_at IS NOT NULL) s ON r.turn_id IS NOT NULL"""
_PREMATURE = """EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = r.conversation_id AND m.direction = 'in' AND m.sender = 'customer'
                          AND m.created_at > r.started_at AND m.created_at <= r.started_at + make_interval(secs => %s))"""


def _ms(a: Any, b: Any) -> int | None:
    return int((b - a).total_seconds() * 1000) if a is not None and b is not None else None


def _pair(v: Any) -> dict[str, int | None]:
    return {"p50": round(v[0]) if v and v[0] is not None else None, "p95": round(v[1]) if v and v[1] is not None else None}


def _pct(part: float, whole: float) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


class AgentAudit:
    def __init__(self, db: Database):
        self.db = db

    async def summary(self, days: int) -> dict[str, Any]:
        since = "r.started_at >= now() - make_interval(days => %s)"
        async with self.db.system_tx() as c:
            counts = await (await c.execute(
                f"""SELECT count(*) AS runs, count(*) FILTER (WHERE r.status = 'running') AS running, count(*) FILTER (WHERE r.status = 'interrupted') AS interrupted,
                           count(*) FILTER (WHERE r.trigger = 'turn_detection') AS turn_detection, count(*) FILTER (WHERE r.trigger = 'scheduler_nudge') AS scheduler_nudge,
                           COALESCE(sum((r.llm->>'cost_micros')::bigint), 0) AS cost,
                           COALESCE(sum((r.llm->>'cost_micros')::bigint) FILTER (WHERE r.outcome IN ('superseded', 'failed', 'no_reply')), 0) AS wasted,
                           COALESCE(sum((r.llm->>'calls')::int), 0) AS calls, COALESCE(sum((r.llm->>'failed')::int), 0) AS failed_calls,
                           count(*) FILTER (WHERE r.trigger = 'turn_detection' AND {_PREMATURE}) AS premature
                      FROM agent_runs r WHERE {since}""", (PREMATURE_WINDOW_S, days))).fetchone()  # noqa: S608 (constant fragments)
            by_outcome = {x["outcome"]: int(x["n"]) for x in await (await c.execute(
                f"SELECT r.outcome, count(*) AS n FROM agent_runs r WHERE {since} AND r.outcome IS NOT NULL GROUP BY r.outcome", (days,))).fetchall()}  # noqa: S608
            lat = await (await c.execute(
                f"""SELECT percentile_cont(ARRAY[0.5, 0.95]) WITHIN GROUP (ORDER BY extract(epoch FROM r.finished_at - r.started_at) * 1000) AS agent,
                           percentile_cont(ARRAY[0.5, 0.95]) WITHIN GROUP (ORDER BY extract(epoch FROM s.first_sent - COALESCE(r.first_inbound_at, r.started_at)) * 1000) AS first_reply,
                           percentile_cont(ARRAY[0.5, 0.95]) WITHIN GROUP (ORDER BY extract(epoch FROM s.last_sent - COALESCE(r.first_inbound_at, r.started_at)) * 1000) AS done,
                           percentile_cont(ARRAY[0.5, 0.95]) WITHIN GROUP (ORDER BY (r.trigger_detail->>'waited_ms')::numeric) AS waited
                      FROM agent_runs r {_TIMING} WHERE {since} AND r.status = 'finished'""", (days,))).fetchone()  # noqa: S608
            stages = await (await c.execute(
                f"""SELECT st.key AS stage, sum((st.value->>'calls')::int) AS calls, sum((st.value->>'failed')::int) AS failed, sum((st.value->>'cost_micros')::bigint) AS cost,
                           sum((st.value->>'ms')::bigint) AS ms, count(*) FILTER (WHERE st.value->>'tier' = 'llm') AS llm_runs, count(*) FILTER (WHERE st.value->>'tier' = 'rules') AS rules_runs
                      FROM agent_runs r, jsonb_each(CASE WHEN jsonb_typeof(r.llm->'by_stage') = 'object' THEN r.llm->'by_stage' ELSE '{{}}'::jsonb END) st
                     WHERE {since} GROUP BY st.key""", (days,))).fetchall()  # noqa: S608
        k = counts or {}
        runs, replied = int(k.get("runs", 0)), by_outcome.get("replied", 0)
        lat = lat or {}
        cost = int(k.get("cost", 0))
        return {
            "days": days, "runs": runs, "running_now": int(k.get("running", 0)), "interrupted": int(k.get("interrupted", 0)),
            "by_outcome": {o: by_outcome.get(o, 0) for o in OUTCOMES},
            "by_trigger": {"turn_detection": int(k.get("turn_detection", 0)), "scheduler_nudge": int(k.get("scheduler_nudge", 0))},
            "cost": {"total_micros": cost, "total_inr": round(cost / 1e6, 4), "per_run_inr": round(cost / 1e6 / runs, 6) if runs else None,
                     "per_reply_inr": round(cost / 1e6 / replied, 6) if replied else None,
                     "wasted_micros": int(k.get("wasted", 0)), "wasted_pct": _pct(int(k.get("wasted", 0)), cost),
                     "note": "Wasted is the model cost of runs that ended with no reply to the customer: overtaken by a newer message, failed, or chose silence."},
            "latency": {"agent_ms": _pair(lat.get("agent")), "first_reply_ms": _pair(lat.get("first_reply")), "done_ms": _pair(lat.get("done")),
                        "note": "first_reply and done run from the customer's first message of the turn to the first and the last reply being sent."},
            "llm": {"calls": int(k.get("calls", 0)), "failed": int(k.get("failed_calls", 0)),
                    "by_stage": {s["stage"]: {"calls": int(s["calls"]), "failed": int(s["failed"] or 0), "cost_micros": int(s["cost"] or 0), "ms": int(s["ms"] or 0),
                                              "llm_runs": int(s["llm_runs"]), "rules_runs": int(s["rules_runs"])} for s in stages}},
            "turn_detection": {"wait_ms": _pair(lat.get("waited")), "premature_runs": int(k.get("premature", 0)),
                               "premature_pct": _pct(int(k.get("premature", 0)), int(k.get("turn_detection", 0))),
                               "superseded_pct": _pct(by_outcome.get("superseded", 0), runs),
                               "note": f"Premature: the customer wrote again within {PREMATURE_WINDOW_S} seconds of the AI starting, so it had not waited for them to finish."},
        }

    async def runs(self, days: int, *, outcome: str | None, trigger: str | None, business_id: uuid.UUID | None, limit: int) -> list[dict[str, Any]]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                f"""SELECT r.id, r.business_id, b.name AS business_name, r.conversation_id, r.trigger, r.status, r.outcome, r.reason, r.started_at, r.finished_at,
                           r.first_inbound_at, r.route->>'intent' AS intent, r.route->'tiers' AS tiers, (r.llm->>'calls')::int AS llm_calls,
                           (r.llm->>'cost_micros')::bigint AS cost_micros, s.first_sent, s.last_sent, {_PREMATURE} AS premature
                      FROM agent_runs r JOIN businesses b ON b.id = r.business_id {_TIMING}
                     WHERE r.started_at >= now() - make_interval(days => %s) AND (%s::text IS NULL OR r.outcome = %s) AND (%s::text IS NULL OR r.trigger = %s)
                       AND (%s::uuid IS NULL OR r.business_id = %s) ORDER BY r.started_at DESC LIMIT %s""",   # noqa: S608
                (PREMATURE_WINDOW_S, days, outcome, outcome, trigger, trigger, business_id, business_id, limit))).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: dict[str, Any]) -> dict[str, Any]:
        base = r["first_inbound_at"] or r["started_at"]
        return {"id": str(r["id"]), "business_id": str(r["business_id"]), "business_name": r["business_name"], "conversation_id": str(r["conversation_id"]),
                "trigger": r["trigger"], "status": r["status"], "outcome": r["outcome"], "reason": r["reason"], "intent": r["intent"], "tiers": r["tiers"] or {},
                "started_at": r["started_at"], "finished_at": r["finished_at"], "llm_calls": r["llm_calls"] or 0,
                "cost_inr": round((r["cost_micros"] or 0) / 1e6, 6), "premature": bool(r["premature"]),
                "agent_ms": _ms(r["started_at"], r["finished_at"]), "first_reply_ms": _ms(base, r["first_sent"]), "done_ms": _ms(base, r["last_sent"])}

    async def run(self, run_id: uuid.UUID) -> dict[str, Any]:
        async with self.db.system_tx() as c:
            r = await (await c.execute(
                f"""SELECT r.*, b.name AS business_name, r.route->>'intent' AS intent, (r.llm->>'calls')::int AS llm_calls, (r.llm->>'cost_micros')::bigint AS cost_micros,
                           s.first_sent, s.last_sent, {_PREMATURE} AS premature
                      FROM agent_runs r JOIN businesses b ON b.id = r.business_id {_TIMING} WHERE r.id = %s""", (PREMATURE_WINDOW_S, run_id))).fetchone()  # noqa: S608
            if r is None:
                raise LookupError("Run not found.")
            msgs = await (await c.execute(
                "SELECT kind, status, created_at, sent_at FROM messages WHERE turn_id = %s AND business_id = %s AND direction = 'out' ORDER BY created_at, id",
                (r["turn_id"], r["business_id"]))).fetchall() if r["turn_id"] else []
        row = {**self._row({**r, "tiers": (r["route"] or {}).get("tiers")}), "route": r["route"], "llm": {k: v for k, v in (r["llm"] or {}).items() if k != "detail"},
               "trigger_detail": r["trigger_detail"]}
        base = r["first_inbound_at"] or r["started_at"]
        td = r["trigger_detail"] or {}
        events: list[tuple[Any, int, dict[str, Any]]] = []
        if r["first_inbound_at"]:
            events.append((r["first_inbound_at"], 0, {"type": "customer_message", "messages": td.get("messages")}))
        events.append((r["started_at"], 1, {"type": "triggered", "trigger": r["trigger"], "eot_reason": td.get("eot_reason"), "waited_ms": td.get("waited_ms"), "rechecks": td.get("rechecks")}))
        events.append((r["started_at"], 2, {"type": "run_started"}))
        clock = r["started_at"]
        for call in (r["llm"] or {}).get("detail", []):               # calls ran one after another: each is placed after the previous one's time
            events.append((clock, 3, {"type": "model_call", "stage": call["stage"], "model": call.get("model"), "tier": "rules" if call.get("provider") in (None, "local", "scripted", "recorder") else "llm",
                                      "ok": call["ok"], "tokens_in": call.get("in"), "tokens_out": call.get("out"), "ms": call.get("ms"), "cost_micros": call.get("cost_micros"),
                                      "cache_read": call.get("cache_read"), "error": call.get("error")}))
            clock += timedelta(milliseconds=int(call.get("ms") or 0))
        if r["route"]:
            events.append((r["finished_at"] or clock, 4, {"type": "decision", **{k: v for k, v in r["route"].items() if k != "tiers"}}))
        if r["finished_at"]:
            events.append((r["finished_at"], 5, {"type": "run_finished", "status": r["status"], "outcome": r["outcome"], "reason": r["reason"]}))
        for m in msgs:
            if m["sent_at"]:
                events.append((m["sent_at"], 6, {"type": "message_sent", "kind": m["kind"]}))
            else:
                events.append((m["created_at"], 6, {"type": "message_unsent", "kind": m["kind"], "status": m["status"]}))
        events.sort(key=lambda e: (e[0], e[1]))
        row["timeline"] = [{"at": at.isoformat(), **ev} for at, _, ev in events]
        row["timing"] = {"waited_ms": td.get("waited_ms"), "agent_ms": _ms(r["started_at"], r["finished_at"]), "llm_ms": (r["llm"] or {}).get("llm_ms"),
                         "first_reply_ms": _ms(base, r["first_sent"]), "done_ms": _ms(base, r["last_sent"])}
        return row
