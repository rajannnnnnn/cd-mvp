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

from salesai.alerts import raise_alert
from salesai.db import Database, jsonb, required
from salesai.events.outbox import announce_change, emit
from salesai.modules.agent import checks as chk
from salesai.modules.agent import context as ctxmod
from salesai.modules.agent import policy as pol
from salesai.modules.agent import prompts
from salesai.modules.agent.holding import holding_message
from salesai.modules.agent.llm import LLMProvider, LLMRequest, LLMUnavailable
from salesai.modules.agent.llm.failure import alert_message, classify
from salesai.modules.agent.models import CheckOutput, PlannerOutput, WriterOutput
from salesai.modules.agent.shape import shape_reply
from salesai.modules.agent.style import reply_hint
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
    planner_effort: str | None = None      # low | medium | high (ADR 0034); None leaves the provider's default
    writer_effort: str | None = None
    check_effort: str | None = None


# Top-level input keys identical across turns for one shop (ADR 0027). They are rendered first, byte-stable, so a prefix
# cache can hit; everything per-turn (the trigger, the clock, the conversation, the plan) must stay out of them, or the
# cache never hits. Guarded by tests/test_llm_caching.py.
PLANNER_CACHE_KEYS: tuple[str, ...] = ("business",)
WRITER_CACHE_KEYS: tuple[str, ...] = ("business_name", "honorific", "style_examples")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    latency_ms: int = 0
    models: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)      # one entry per model call, in order (audit)

    def add(self, res: Any, stage: str = "?") -> None:
        self.input_tokens += res.input_tokens
        self.output_tokens += res.output_tokens
        self.cost_micros += res.cost_micros
        self.latency_ms += res.latency_ms
        if res.model not in self.models:
            self.models.append(res.model)
        self.calls.append({"stage": stage, "ok": True, "model": res.model, "provider": getattr(res, "provider", None),
                           "in": res.input_tokens, "out": res.output_tokens, "ms": res.latency_ms, "cost_micros": res.cost_micros,
                           # cache hits per call, so whether caching works is visible in every turn record (ADR 0027)
                           "cache_read": getattr(res, "cache_read_tokens", 0), "cache_write": getattr(res, "cache_write_tokens", 0)})

    def failed(self, stage: str, error: Any) -> None:
        self.calls.append({"stage": stage, "ok": False, "error": str(error)[:160]})

    def consolidated(self) -> dict[str, Any]:
        """Every model call of one run added up and split by step (the agent audit, ADR 0037). Counts, tokens, milliseconds, money and
        which tier answered (rules or a language model): nothing of what was said."""
        by_stage: dict[str, dict[str, Any]] = {}
        for c in self.calls:
            st = by_stage.setdefault(c["stage"], {"calls": 0, "failed": 0, "in": 0, "out": 0, "ms": 0, "cost_micros": 0, "tier": "rules"})
            st["calls"] += 1
            if not c["ok"]:
                st["failed"] += 1
                continue
            st["in"] += c["in"]
            st["out"] += c["out"]
            st["ms"] += c["ms"]
            st["cost_micros"] += c.get("cost_micros", 0)
            if c.get("provider") not in (None, "local", "scripted", "recorder"):
                st["tier"] = "llm"
        return {"calls": len(self.calls), "failed": sum(1 for c in self.calls if not c["ok"]), "models": list(self.models), "by_stage": by_stage,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "llm_ms": self.latency_ms, "cost_micros": self.cost_micros,
                "cache_read_tokens": sum(c.get("cache_read", 0) for c in self.calls), "cache_write_tokens": sum(c.get("cache_write", 0) for c in self.calls),
                "detail": list(self.calls)}

    def summary(self) -> dict[str, Any]:
        """What the audit shows: how many model calls this turn made, split by step, and which of them failed."""
        by_stage: dict[str, int] = {}
        for c in self.calls:
            by_stage[c["stage"]] = by_stage.get(c["stage"], 0) + 1
        return {"calls": len(self.calls), "failed": sum(1 for c in self.calls if not c["ok"]), "by_stage": by_stage, "detail": self.calls}


@dataclass
class TurnOutcome:
    status: str                       # committed | silent | blocked | superseded | limit
    turn_id: uuid.UUID | None = None
    reason: str | None = None
    parts: list[str] = field(default_factory=list)
    intent: str | None = None
    route: dict[str, Any] = field(default_factory=dict)      # how the run routed and decided: no customer words, no prices (the audit, ADR 0037)
    llm: dict[str, Any] = field(default_factory=dict)        # every model call of the run, consolidated: kept even when the run is discarded
    reaction_only: bool = False


# Replies that state nothing the deterministic checks cannot see: the optional model check (is anything invented?) is skipped for them. A directive
# nobody classified here is treated as risky, never as safe (ADR 0039).
LOW_RISK_DIRECTIVES = frozenset({"greeting", "thanks", "goodbye", "ack", "ask_product", "ask_qualify", "handoff_notice", "decline_out_of_scope",
                                 "unsupported_media", "not_interested", "identity", "nudge", "clarify", "visit_ask_time", "smalltalk"})


def needs_model_check(plan: pol.Plan) -> bool:
    return any(d["type"] not in LOW_RISK_DIRECTIVES for d in plan.directives)


def route_of(plan: pol.Plan, usage: Usage, *, checks: dict[str, Any] | None = None, fallback: str | None = None) -> dict[str, Any]:
    """The routing and decisions of one run, for the audit: names and counts, never words or amounts."""
    failed = sorted({f.split(":", 1)[0] for a in (checks or {}).get("attempts", []) for f in a.get("failures", [])})
    stages = usage.consolidated()["by_stage"]
    return {"intent": plan.intent, "directives": [d["type"] for d in plan.directives], "handoffs": [r for r, _ in plan.handoffs],
            "lead_stage": plan.lead_stage, "language": plan.language, "script": plan.script, "silent": plan.silent, "reaction": bool(plan.reaction_emoji),
            "deals": [d.kind for d in plan.deals], "gaps": len(plan.gaps), "selling_stopped": plan.selling_stopped,
            "pricing": [{"kind": e.decision.kind, "final_offer": not e.decision.can_concede_more, "handoff": e.decision.handoff_reason is not None} for e in plan.evaluations],
            "checks": {"attempts": len((checks or {}).get("attempts", [])), "failed": failed},
            "fallback": fallback,                                    # llm_unavailable | checks_failed | daily_limit: a safe holding reply was used
            "tiers": {"turn_detection": "rules", **{st: v["tier"] for st, v in stages.items()}}}


class AgentService:
    def __init__(self, db: Database, llm: LLMProvider, pricing: PricingService, models: Models, *,
                 llm_checks: bool = True, skip_check_when_low_risk: bool = True, rng: random.Random | None = None):
        self.db, self.llm, self.pricing, self.models = db, llm, pricing, models
        self.llm_checks = llm_checks
        self.skip_check_when_low_risk = skip_check_when_low_risk
        self.rng = rng
        self._recovery_checked: dict[str, float] = {}      # step -> when its failure alerts were last checked for closing

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
                return await self._finish(ctx, plan, ["_limit"], Usage(), {"limit": True}, signals or {}, version, t0, trigger, fallback="daily_limit")

            usage = Usage()
            prompt_refs: dict[str, str] = {}
            try:
                plan, out = await self._plan(ctx, usage, prompt_refs, trigger)
            except LLMUnavailable as e:
                usage.failed("planner", e)
                log.error("LLM unavailable at planning: %s", e)
                await self._report_llm_failure("planner", e)
                plan = self._holding_plan(ctx, f"LLM unavailable: {e}", "system_failure")
                return await self._finish(ctx, plan, [self._holding_text(ctx, plan)], usage, {"llm_unavailable": True}, signals or {}, version, t0, trigger, prompt_refs,
                                          fallback="llm_unavailable")

            if await self._conversation_moved(business_id, conversation_id, version):
                # Cost control and UX: the customer wrote again while the planner was thinking, so this answer is already stale (INV-7).
                # Stop before the writer, the expensive and slower step, instead of paying for a reply nobody will see.
                return TurnOutcome("superseded", reason="the customer wrote again while the AI was planning; stopped before writing",
                                   route=route_of(plan, usage), llm=usage.consolidated())

            typing_started = await self._start_typing(ctx, plan, version, trigger)     # "typing..." while the words are being written, not after
            parts, reaction, checks_log = await self._write_and_check(ctx, plan, usage, prompt_refs)
            fallback = None
            if parts is None:      # checks failed twice or provider down: safe holding message + handoff
                fallback = "llm_unavailable" if "llm_unavailable" in checks_log.get("failed", []) else "checks_failed"
                plan = self._holding_plan(ctx, "reply failed automated checks: " + ",".join(checks_log.get("failed", [])), "system_failure", keep=plan)
                parts, reaction = [self._holding_text(ctx, plan)], None
            return await self._finish(ctx, plan, parts, usage, {"planner": out.model_dump(mode="json"), "checks": checks_log},
                                      signals or {}, version, t0, trigger, prompt_refs, reaction=reaction, fallback=fallback, typing_started=typing_started)

    async def _report_llm_failure(self, stage: str, error: Exception) -> None:
        """Tell the founder, with the specific reason, that a model call failed (ADR 0049). One platform-wide alert per step: repeats only count up.
        Alerting must never break a turn, so any failure here is logged and swallowed."""
        f = classify(error, stage)
        try:
            async with self.db.system_tx() as s:
                await raise_alert(s, f.severity, f"llm_{f.reason}", alert_message(f, stage, error), dedup_key=stage,
                                  detail={"stage": stage, "reason": f.reason})
        except Exception:  # noqa: BLE001
            log.exception("could not raise the model-failure alert")

    async def _llm_ok(self, stage: str) -> None:
        """A model call for this step worked: close its open failure alerts so the founder gets the recovery notice. At most one cheap update a minute per step."""
        now = time.monotonic()
        if now - self._recovery_checked.get(stage, -1e9) < 60:
            return
        self._recovery_checked[stage] = now
        try:
            async with self.db.system_tx() as s:
                await s.execute("UPDATE operator_alerts SET resolved_at = now() WHERE kind LIKE 'llm\\_%%' AND dedup_key = %s AND resolved_at IS NULL", (stage,))
        except Exception:  # noqa: BLE001
            log.exception("could not close the model-failure alert")

    async def _start_typing(self, ctx: ctxmod.TurnContext, plan: pol.Plan, version: int, trigger: str) -> float | None:
        """Show "typing..." as soon as the AI knows it will reply, so the customer's wait while the model writes looks like someone
        typing rather than silence. Not for a nudge (no message to answer), and never when it will say nothing."""
        if trigger == "nudge" or not plan.directives:
            return None
        last_wa = next((m["wa_message_id"] for m in reversed(ctx.unanswered) if m["wa_message_id"]), None)
        if not last_wa:
            return None
        bid, cid = ctx.business["id"], ctx.conv["id"]
        async with self.db.tenant(bid) as c:
            await emit(c, "outbound.action_requested", {"conversation_id": cid, "action": "typing", "version": version, "inbound_wa_id": last_wa,
                                                        "allow_while_paused": bool(plan.handoffs)},
                       business_id=bid, ordering_key=f"conv:{cid}", entity_key=f"conversation:{cid}", entity_version=version)
        return time.perf_counter()

    async def _conversation_moved(self, business_id: uuid.UUID, conversation_id: uuid.UUID, version: int) -> bool:
        async with self.db.tenant(business_id) as c:
            row = await (await c.execute("SELECT version FROM conversations WHERE id=%s", (conversation_id,))).fetchone()
        return row is None or row["version"] != version

    # ------------------------------------------------------------------ steps
    async def _plan(self, ctx: ctxmod.TurnContext, usage: Usage, prompt_refs: dict[str, str], trigger: str) -> tuple[pol.Plan, PlannerOutput]:
        pr = prompts.load("planner")
        prompt_refs["planner"] = pr.ref
        res = await self.llm.generate(LLMRequest("planner", pr.text, ctx.planner_input(trigger), self.models.planner, pr.ref, max_tokens=1200,
                                                  cache_keys=PLANNER_CACHE_KEYS, effort=self.models.planner_effort), PlannerOutput)
        usage.add(res, "planner")
        await self._llm_ok("planner")
        out: PlannerOutput = res.output
        plan = await pol.build_plan(ctx, out, self.pricing, trigger=trigger)
        return plan, out

    def _checkctx(self, ctx: ctxmod.TurnContext, plan: pol.Plan) -> chk.CheckContext:
        allowed = chk.numbers_in(" ".join(plan.allowed_texts))
        return chk.CheckContext(issued=frozenset(plan.issued), issued_percents=frozenset(plan.issued_percents),
                                allowed_numbers=frozenset(allowed), real_urgency=frozenset(plan.real_urgency),
                                sincere_identity_question=plan.sincere_identity_question, customer_script=plan.script, language=plan.language,
                                customer_style=ctx.customer_style)

    async def _write_and_check(self, ctx: ctxmod.TurnContext, plan: pol.Plan, usage: Usage, prompt_refs: dict[str, str]
                               ) -> tuple[list[str] | None, str | None, dict[str, Any]]:
        if plan.silent:
            return [], None, {"silent": True}
        if not plan.directives:       # reaction only
            return [], plan.reaction_emoji, {"reaction_only": True}
        style = ctx.customer_style
        wp = prompts.load("writer")
        prompt_refs["writer"] = wp.ref
        sales = ctx.business["sales_settings"] or {}
        base_input = {
            "language": plan.language, "script": plan.script, "business_name": ctx.business["name"],
            "customer_name": ctx.customer.get("name"), "honorific": sales.get("honorific"),
            "style_examples": [{"situation": e["situation"], "customer_text": e["customer_text"], "owner_reply": e["owner_reply"]} for e in ctx.style_examples],
            "directives": plan.directives, "reaction_emoji": plan.reaction_emoji,
            # an ongoing chat: without these the writer treats every turn as the first and greets again
            "first_reply": not ctx.has_prior_ai_message,
            "history": [{"sender": m["sender"], "text": (m["body"] or "")[:400]} for m in ctx.history[-12:]],
            "customer_just_said": [(m["body"] or "")[:400] for m in ctx.unanswered],
            # how this customer writes, and what the reply is asked to match (ADR 0041): language mix, how casual, how long, emoji
            "customer_style": style, "reply_hint": reply_hint(style),
        }
        checkctx = self._checkctx(ctx, plan)
        log: dict[str, Any] = {"attempts": []}
        feedback: list[str] = []
        for _ in range(2):
            try:
                res = await self.llm.generate(
                    LLMRequest("writer", wp.text, {**base_input, **({"feedback": feedback} if feedback else {})}, self.models.writer, wp.ref, max_tokens=900,
                               cache_keys=WRITER_CACHE_KEYS, effort=self.models.writer_effort),
                    WriterOutput)
            except LLMUnavailable as e:
                usage.failed("writer", e)
                await self._report_llm_failure("writer", e)
                log["failed"], log["error"] = ["llm_unavailable"], str(e)
                return None, None, log
            usage.add(res, "writer")
            await self._llm_ok("writer")
            parts = [p.strip() for p in res.output.parts if p and p.strip()]
            result = chk.run_checks(parts, checkctx)
            # in a language the word patterns do not know, the model check is the only judge of "is this a person?" and "does it say it is an assistant?"
            if result.ok and self.llm_checks and (needs_model_check(plan) or not chk.lexical(plan.language) or not self.skip_check_when_low_risk):
                result = await self._llm_check(ctx, plan, parts, usage, prompt_refs, result)
            log["attempts"].append({"ok": result.ok, "failures": [f"{f.check}: {f.detail}" for f in result.failures]})
            if result.ok:
                return parts, res.output.reaction_emoji or plan.reaction_emoji, log
            for name in result.checks():
                VALIDATOR_REJECTIONS.labels(name).inc()
            feedback = [f"{f.check}: {f.detail}" for f in result.failures]
            log["failed"] = result.checks()
        return None, None, log

    async def _llm_check(self, ctx: ctxmod.TurnContext, plan: pol.Plan, parts: list[str], usage: Usage, prompt_refs: dict[str, str],
                         result: chk.CheckResult) -> chk.CheckResult:
        cp = prompts.load("checker")
        prompt_refs["checker"] = cp.ref
        try:
            res = await self.llm.generate(
                LLMRequest("check", cp.text, {"parts": parts, "business": ctx.business["name"],
                                              # the same facts the writer was given: engine-issued prices, stock, shop info. Without them every correct quote looks invented.
                                              "allowed_facts": plan.directives,
                                              "language": plan.language, "must_disclose": plan.sincere_identity_question,
                                              "facts": ctxmod.topic_text(ctx.business["profile"] or {}, "hours")}, self.models.check, cp.ref, max_tokens=400,
                           effort=self.models.check_effort), CheckOutput)
        except LLMUnavailable as e:
            usage.failed("check", e)
            await self._report_llm_failure("check", e)
            return result                      # deterministic checks already passed; do not block on the optional model check
        usage.add(res, "check")
        await self._llm_ok("check")
        o: CheckOutput = res.output
        if not o.scope_ok:
            result.failures.append(chk.Failure("scope", "reply is outside the business scope"))
        if o.claims_human:
            result.failures.append(chk.Failure("human_claim", "model check: reply implies a human"))
        if plan.sincere_identity_question and not o.identity_ok:
            result.failures.append(chk.Failure("identity", "model check: the customer asked if this is a bot; the reply must say it is the shop's assistant"))
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

    def _holding_text(self, ctx: ctxmod.TurnContext, plan: pol.Plan) -> str:
        """The one message sent before the hand-over when the AI cannot answer: names who the customer is being connected to (agent/holding.py)."""
        sales = ctx.business["sales_settings"] or {}
        return holding_message([m["body"] for m in ctx.unanswered if m.get("body")], owner_name=ctx.extra.get("owner_name"),
                               owner_title=sales.get("owner_title"), last_language=ctx.qualification.get("last_language"),
                               script_hint=ctx.customer_style.get("script"))

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
                      *, reaction: str | None = None, fallback: str | None = None, typing_started: float | None = None) -> TurnOutcome:
        bid, cid = ctx.business["id"], ctx.conv["id"]
        route, llm_usage = route_of(plan, usage, checks=decision.get("checks"), fallback=fallback), usage.consolidated()
        parts = [p for p in parts if p != "_limit"] or ([self._holding_text(ctx, plan)] if plan.handoffs else [])
        now = datetime.now(UTC)
        timing = ctx.business["timing_params"]
        csettings = ctx.business["conversation_settings"] or {}
        # one message unless the answer is long or the customer writes in short bursts (ADR 0026); words never change
        parts = shape_reply(parts, [m["body"] or "" for m in ctx.unanswered if m.get("kind") == "text"],
                            max_parts=int(csettings.get("max_reply_parts", 3)),
                            split_over_chars=int(csettings.get("split_over_chars", 320)),
                            burst_chars=int(csettings.get("burst_message_chars", 80)))
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
        # the read receipt already went out when the customer stopped writing, and typing has been showing since the AI decided to reply:
        # that time counts towards the typing time, so the model's own latency is not added on top of a human-looking pause
        typing_for = None if typing_started is None else int((time.perf_counter() - typing_started) * 1000)
        acts = plan_delivery(parts, reaction, timing, rng=self.rng, multiplier=mult, offset_ms=offset,
                             include_read=not signals.get("read_sent"), elapsed_ms=typing_for) if (parts or reaction) else []
        inbound_ids = [m["id"] for m in ctx.unanswered]
        last_inbound_wa = next((m["wa_message_id"] for m in reversed(ctx.unanswered) if m["wa_message_id"]), None)

        async with self.db.tenant(bid) as c:
            conv = required(await (await c.execute("SELECT * FROM conversations WHERE id=%s FOR UPDATE", (cid,))).fetchone(), "conv")
            if conv["version"] != version:
                return TurnOutcome("superseded", reason="conversation moved on before commit (INV-7)", route=route, llm=llm_usage)
            if ai_block_reason(business=ctx.business, conv=conv, customer=ctx.customer, number=ctx.number, now=now) and not plan.handoffs:
                return TurnOutcome("blocked", reason="blocked at commit", route=route, llm=llm_usage)
            turn = await (await c.execute(
                """INSERT INTO turns (business_id, conversation_id, conversation_version, inbound_message_ids, status, decision, signals,
                                      model, prompt_versions, input_tokens, output_tokens, cost_micros, latency_ms, completed_at)
                   VALUES (%s,%s,%s,%s,'planned',%s,%s,%s,%s,%s,%s,%s,%s, now()) RETURNING id""",
                (bid, cid, version, inbound_ids, jsonb({**decision, "intent": plan.intent, "llm": usage.summary(), "directives": [d["type"] for d in plan.directives],
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

            key = f"conv:{cid}"          # ordered per conversation, never per number: one chatty customer must not delay the rest
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
            await announce_change(c, bid, cid)                                   # lead stage and a reply in flight changed: re-plan the clock
            await c.execute("UPDATE customers SET profile = COALESCE(profile, '{}'::jsonb) || %s WHERE id=%s",
                            (jsonb({"style": ctx.customer_style}), ctx.customer["id"]))      # how they write, kept with the customer (ADR 0041)
        TURN_SECONDS.observe(time.perf_counter() - t0)
        return TurnOutcome("committed" if acts else "silent", turn_id=tid, parts=parts, intent=plan.intent, route=route, llm=llm_usage,
                           reaction_only=bool(acts) and not parts)


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
