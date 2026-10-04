"""The agent pipeline: planner -> pricing engine -> writer -> reply checks -> commit.

Properties (Technical Design: LLM orchestration):
  * model output is structured and schema-validated; the model proposes, code validates and executes
  * every outbound reply passes automated checks; a failure leads to ONE regeneration, then a safe holding
    message plus handoff (INV-2, INV-9, CR-8)
  * the commit re-checks the conversation version under a row lock and discards a stale turn (INV-7)
  * each turn records decision, prompt versions, models, tokens, cost and latency (FR-OP-2, NFR-14)
  * provider failure ends in a holding message + handoff, never silence (INV-12)
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from salesai.db import Database, jsonb, required
from salesai.events.outbox import emit
from salesai.modules.agent import checks as chk
from salesai.modules.agent import context as ctxmod
from salesai.modules.agent import policy as pol
from salesai.modules.agent import prompts
from salesai.modules.agent.llm import LLMProvider, LLMRequest, LLMUnavailable
from salesai.modules.agent.llm.local_nlg import render as render_directive
from salesai.modules.agent.models import CheckOutput, PlannerOutput, WriterOutput
from salesai.modules.delivery import is_open, next_open, plan_delivery
from salesai.modules.handoffs import create_handoff, log_gap
from salesai.modules.pricing import PricingService
from salesai.modules.sales import capture_deal
from salesai.obs import TURN_SECONDS, VALIDATOR_REJECTIONS, bind
from salesai.rules import ai_block_reason, window_closes_at

log = logging.getLogger("salesai.agent")


@dataclass
class Models:
    planner: str
    writer: str
    check: str


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    latency_ms: int = 0
    models: list[str] = field(default_factory=list)

    def add(self, res: Any) -> None:
        self.input_tokens += res.input_tokens
        self.output_tokens += res.output_tokens
        self.cost_micros += res.cost_micros
        self.latency_ms += res.latency_ms
        if res.model not in self.models:
            self.models.append(res.model)


@dataclass
class TurnOutcome:
    status: str                       # committed | silent | blocked | superseded | limit
    turn_id: uuid.UUID | None = None
    reason: str | None = None
    parts: list[str] = field(default_factory=list)
    intent: str | None = None


class AgentService:
    def __init__(self, db: Database, llm: LLMProvider, pricing: PricingService, models: Models, *,
                 llm_checks: bool = True, rng: random.Random | None = None):
        self.db, self.llm, self.pricing, self.models = db, llm, pricing, models
        self.llm_checks = llm_checks
        self.rng = rng

    # ------------------------------------------------------------------ public
    async def run_turn(self, business_id: uuid.UUID, conversation_id: uuid.UUID, version: int, *,
                       trigger: str = "reply", signals: dict[str, Any] | None = None) -> TurnOutcome:
        t0 = time.perf_counter()
        now = datetime.now(UTC)
        with bind(business_id=business_id, conversation_id=conversation_id):
            ctx = await ctxmod.load(self.db, business_id, conversation_id, now)
            if ctx.conv["version"] != version:
                return TurnOutcome("superseded", reason="version moved before planning")
            block = ai_block_reason(business=ctx.business, conv=ctx.conv, customer=ctx.customer, number=ctx.number, now=now)
            if block:
                await self._release(ctx, block)
                return TurnOutcome("blocked", reason=block)
            if await self._over_limits(ctx):
                plan = self._holding_plan(ctx, "daily limit reached", "other")
                return await self._finish(ctx, plan, ["_limit"], Usage(), {"limit": True}, signals or {}, version, t0, trigger)

            usage = Usage()
            prompt_refs: dict[str, str] = {}
            try:
                plan, out = await self._plan(ctx, usage, prompt_refs, trigger)
            except LLMUnavailable as e:
                log.error("LLM unavailable at planning: %s", e)
                plan = self._holding_plan(ctx, f"LLM unavailable: {e}", "system_failure")
                return await self._finish(ctx, plan, [self._holding_text(plan)], usage, {"llm_unavailable": True}, signals or {}, version, t0, trigger, prompt_refs)

            parts, reaction, checks_log = await self._write_and_check(ctx, plan, usage, prompt_refs)
            if parts is None:      # checks failed twice or provider down: safe holding message + handoff
                plan = self._holding_plan(ctx, "reply failed automated checks: " + ",".join(checks_log.get("failed", [])), "system_failure", keep=plan)
                parts, reaction = [self._holding_text(plan)], None
            return await self._finish(ctx, plan, parts, usage, {"planner": out.model_dump(mode="json"), "checks": checks_log},
                                      signals or {}, version, t0, trigger, prompt_refs, reaction=reaction)

    # ------------------------------------------------------------------ steps
    async def _plan(self, ctx: ctxmod.TurnContext, usage: Usage, prompt_refs: dict[str, str], trigger: str) -> tuple[pol.Plan, PlannerOutput]:
        pr = prompts.load("planner")
        prompt_refs["planner"] = pr.ref
        res = await self.llm.generate(LLMRequest("planner", pr.text, ctx.planner_input(trigger), self.models.planner, pr.ref, max_tokens=1200), PlannerOutput)
        usage.add(res)
        out: PlannerOutput = res.output
        plan = await pol.build_plan(ctx, out, self.pricing, trigger=trigger)
        return plan, out

    def _checkctx(self, ctx: ctxmod.TurnContext, plan: pol.Plan) -> chk.CheckContext:
        allowed = chk.numbers_in(" ".join(plan.allowed_texts))
        return chk.CheckContext(issued=frozenset(plan.issued), issued_percents=frozenset(plan.issued_percents),
                                allowed_numbers=frozenset(allowed), real_urgency=frozenset(plan.real_urgency),
                                sincere_identity_question=plan.sincere_identity_question, customer_script=plan.script)

    async def _write_and_check(self, ctx: ctxmod.TurnContext, plan: pol.Plan, usage: Usage, prompt_refs: dict[str, str]
                               ) -> tuple[list[str] | None, str | None, dict[str, Any]]:
        if plan.silent:
            return [], None, {"silent": True}
        if not plan.directives:       # reaction only
            return [], plan.reaction_emoji, {"reaction_only": True}
        wp = prompts.load("writer")
        prompt_refs["writer"] = wp.ref
        sales = ctx.business["sales_settings"] or {}
        base_input = {
            "language": plan.language, "script": plan.script, "business_name": ctx.business["name"],
            "customer_name": ctx.customer.get("name"), "honorific": sales.get("honorific"),
            "style_examples": [{"situation": e["situation"], "customer_text": e["customer_text"], "owner_reply": e["owner_reply"]} for e in ctx.style_examples],
            "directives": plan.directives, "reaction_emoji": plan.reaction_emoji,
        }
        checkctx = self._checkctx(ctx, plan)
        log: dict[str, Any] = {"attempts": []}
        feedback: list[str] = []
        for _ in range(2):
            try:
                res = await self.llm.generate(
                    LLMRequest("writer", wp.text, {**base_input, **({"feedback": feedback} if feedback else {})}, self.models.writer, wp.ref, max_tokens=900),
                    WriterOutput)
            except LLMUnavailable as e:
                log["failed"], log["error"] = ["llm_unavailable"], str(e)
                return None, None, log
            usage.add(res)
            parts = [p.strip() for p in res.output.parts if p and p.strip()]
            result = chk.run_checks(parts, checkctx)
            if result.ok and self.llm_checks:
                result = await self._llm_check(ctx, parts, usage, prompt_refs, result)
            log["attempts"].append({"ok": result.ok, "failures": [f"{f.check}: {f.detail}" for f in result.failures]})
            if result.ok:
                return parts, res.output.reaction_emoji or plan.reaction_emoji, log
            for name in result.checks():
                VALIDATOR_REJECTIONS.labels(name).inc()
            feedback = [f"{f.check}: {f.detail}" for f in result.failures]
            log["failed"] = result.checks()
        return None, None, log

    async def _llm_check(self, ctx: ctxmod.TurnContext, parts: list[str], usage: Usage, prompt_refs: dict[str, str],
                         result: chk.CheckResult) -> chk.CheckResult:
        cp = prompts.load("checker")
        prompt_refs["checker"] = cp.ref
        try:
            res = await self.llm.generate(
                LLMRequest("check", cp.text, {"parts": parts, "business": ctx.business["name"],
                                              "facts": ctxmod.topic_text(ctx.business["profile"] or {}, "hours")}, self.models.check, cp.ref, max_tokens=400), CheckOutput)
        except LLMUnavailable:
            return result                      # deterministic checks already passed; do not block on the optional model check
        usage.add(res)
        o: CheckOutput = res.output
        if not o.scope_ok:
            result.failures.append(chk.Failure("scope", "reply is outside the business scope"))
        if o.claims_human:
            result.failures.append(chk.Failure("human_claim", "model check: reply implies a human"))
        for claim in o.invented_claims[:3]:
            result.failures.append(chk.Failure("invented_claim", claim[:120]))
        return result

    def _holding_plan(self, ctx: ctxmod.TurnContext, note: str, reason: str, keep: pol.Plan | None = None) -> pol.Plan:
        """Safe fallback: no numbers, no model, an honest holding message and a handoff to the owner (INV-12)."""
        base = keep or pol.Plan(intent="holding", lead_stage=ctx.conv["lead_stage"], qualification=dict(ctx.qualification),
                                language="en", script="latin")
        p = pol.Plan(intent="holding", lead_stage=base.lead_stage, qualification=base.qualification, language=base.language,
                     script=base.script, selling_stopped=base.selling_stopped)
        p.handoffs.append((reason, note))
        return p

    def _holding_text(self, plan: pol.Plan) -> str:
        return render_directive({"type": "handoff_notice", "reason": "system_failure"}, plan.language,
                                {"business_name": None, "customer_name": None, "honorific": None})

    async def _release(self, ctx: ctxmod.TurnContext, reason: str) -> None:
        """AI may not speak: record the messages as handled-by-others so a later turn does not answer stale text."""
        async with self.db.tenant(ctx.business["id"]) as c:
            await c.execute("UPDATE messages SET answered=true WHERE conversation_id=%s AND direction='in' AND NOT answered", (ctx.conv["id"],))
            await c.execute("UPDATE conversations SET state='idle' WHERE id=%s AND version=%s", (ctx.conv["id"], ctx.conv["version"]))

    async def _over_limits(self, ctx: ctxmod.TurnContext) -> bool:
        limits = ctx.business["limits"] or {}
        async with self.db.tenant(ctx.business["id"]) as c:
            tok = (await (await c.execute(
                "SELECT COALESCE(sum(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)),0) AS n FROM turns WHERE created_at > date_trunc('day', now())")).fetchone())["n"]
        return int(tok) >= int(limits.get("llm_tokens_per_day", 10**12))

    # ------------------------------------------------------------------ commit
    async def _finish(self, ctx: ctxmod.TurnContext, plan: pol.Plan, parts: list[str], usage: Usage, decision: dict[str, Any],
                      signals: dict[str, Any], version: int, t0: float, trigger: str, prompt_refs: dict[str, str] | None = None,
                      *, reaction: str | None = None) -> TurnOutcome:
        bid, cid = ctx.business["id"], ctx.conv["id"]
        parts = [p for p in parts if p != "_limit"] or ([self._holding_text(plan)] if plan.handoffs else [])
        now = datetime.now(UTC)
        timing = ctx.business["timing_params"]
        csettings = ctx.business["conversation_settings"] or {}
        mult, offset = 1.0, 0
        behavior = csettings.get("business_hours_behavior", "reply_normally")
        if behavior != "reply_normally" and not is_open(ctx.business["profile"] or {}, ctx.business["timezone"], now):
            if behavior == "slower":
                mult = float(timing.get("slower_multiplier", 2.5))
            elif behavior == "wait_for_open":
                nxt = next_open(ctx.business["profile"] or {}, ctx.business["timezone"], now)
                close = window_closes_at(ctx.conv["last_inbound_at"])
                if nxt and close and nxt + timedelta(minutes=5) < close - timedelta(minutes=10):
                    offset = int((nxt - now).total_seconds() * 1000)
        acts = plan_delivery(parts, reaction, timing, rng=self.rng, multiplier=mult, offset_ms=offset) if (parts or reaction) else []
        inbound_ids = [m["id"] for m in ctx.unanswered]
        last_inbound_wa = next((m["wa_message_id"] for m in reversed(ctx.unanswered) if m["wa_message_id"]), None)

        async with self.db.tenant(bid) as c:
            conv = required(await (await c.execute("SELECT * FROM conversations WHERE id=%s FOR UPDATE", (cid,))).fetchone(), "conv")
            if conv["version"] != version:
                return TurnOutcome("superseded", reason="conversation moved on before commit (INV-7)")
            if ai_block_reason(business=ctx.business, conv=conv, customer=ctx.customer, number=ctx.number, now=now) and not plan.handoffs:
                return TurnOutcome("blocked", reason="blocked at commit")
            turn = await (await c.execute(
                """INSERT INTO turns (business_id, conversation_id, conversation_version, inbound_message_ids, status, decision, signals,
                                      model, prompt_versions, input_tokens, output_tokens, cost_micros, latency_ms, completed_at)
                   VALUES (%s,%s,%s,%s,'planned',%s,%s,%s,%s,%s,%s,%s,%s, now()) RETURNING id""",
                (bid, cid, version, inbound_ids, jsonb({**decision, "intent": plan.intent, "directives": [d["type"] for d in plan.directives],
                                                       "engine": [e.decision.for_llm() for e in plan.evaluations]}),
                 jsonb({**signals, "trigger": trigger}), ",".join(usage.models) or None, jsonb(prompt_refs or {}),
                 usage.input_tokens, usage.output_tokens, usage.cost_micros, usage.latency_ms))).fetchone()
            tid = turn["id"]
            await c.execute("UPDATE messages SET answered=true, turn_id=%s WHERE id = ANY(%s)", (tid, inbound_ids))
            for ev in plan.evaluations:
                await self.pricing.save_in(c, bid, cid, ev)

            msg_ids: dict[int, uuid.UUID] = {}
            reaction_msg: uuid.UUID | None = None
            for a in acts:
                if a.action == "send_text":
                    r = await (await c.execute(
                        "INSERT INTO messages (business_id, conversation_id, turn_id, direction, sender, kind, body, status) VALUES (%s,%s,%s,'out','ai','text',%s,'queued') RETURNING id",
                        (bid, cid, tid, a.text))).fetchone()
                    msg_ids[a.part_index or 0] = r["id"]
                elif a.action == "send_reaction" and last_inbound_wa:
                    r = await (await c.execute(
                        "INSERT INTO messages (business_id, conversation_id, turn_id, direction, sender, kind, body, status, reply_to_wa_id) VALUES (%s,%s,%s,'out','ai','reaction',%s,'queued',%s) RETURNING id",
                        (bid, cid, tid, a.emoji, last_inbound_wa))).fetchone()
                    reaction_msg = r["id"]

            for reason, note in plan.handoffs:
                await create_handoff(c, bid, cid, reason, note, pause_minutes=int(csettings.get("owner_pause_minutes", 120)))
            for q in plan.gaps:
                await log_gap(c, bid, cid, q)
            for d in plan.deals:
                await capture_deal(c, bid, cid, d.kind, d.items, d.details, d.value)

            summary = _summary(plan, ctx)
            await c.execute(
                """UPDATE conversations SET lead_stage=%s, lost_reason=COALESCE(%s, lost_reason), qualification=%s, selling_stopped=%s,
                          summary=%s, state=%s WHERE id=%s""",
                (plan.lead_stage, plan.lost_reason, jsonb(plan.qualification), plan.selling_stopped, summary,
                 "composing" if acts else "idle", cid))

            key = f"num:{ctx.number['id']}"
            for a in acts:
                run_at = now + timedelta(milliseconds=a.delay_ms)
                payload: dict[str, Any] = {"conversation_id": cid, "turn_id": tid, "action": a.action, "version": version,
                                           "allow_while_paused": bool(plan.handoffs)}   # the handoff notice precedes the pause it sets
                if a.action == "send_text":
                    payload.update(message_id=msg_ids[a.part_index or 0])
                    payload["last_part"] = a.last_part
                elif a.action == "send_reaction":
                    if reaction_msg is None:
                        continue
                    payload.update(message_id=reaction_msg, emoji=a.emoji, inbound_wa_id=last_inbound_wa)
                else:
                    payload.update(inbound_wa_id=last_inbound_wa)
                await emit(c, "outbound.action_requested", payload, business_id=bid, ordering_key=key,
                           entity_key=f"conversation:{cid}", entity_version=version, run_at=run_at)
            await emit(c, "turn.decided", {"turn_id": tid, "conversation_id": cid, "action": "reply" if parts else "silent", "intent": plan.intent},
                       business_id=bid)
        TURN_SECONDS.observe(time.perf_counter() - t0)
        return TurnOutcome("committed" if acts else "silent", turn_id=tid, parts=parts, intent=plan.intent)


def _summary(plan: pol.Plan, ctx: ctxmod.TurnContext) -> str:
    q = plan.qualification
    bits = [f"stage={plan.lead_stage}"]
    foc = ctx.candidate(q["focus_variant_id"]) if q.get("focus_variant_id") else None
    if foc:
        bits.append(f"interested in {foc['product_name']}")
    for k in ("quantity", "occasion", "timeline", "delivery_location"):
        if q.get(k):
            bits.append(f"{k}={q[k]}")
    if q.get("price_quoted"):
        bits.append("price discussed")
    if q.get("pending_order"):
        bits.append("order pending confirmation")
    return "; ".join(bits)
