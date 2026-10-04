"""Conversation evaluation suite (Technical Design: required for prompt, model or pipeline changes).
Every scenario runs through the REAL stack: signed webhook -> ingress -> queue -> router -> end-of-turn -> agent pipeline ->
pricing engine -> reply checks -> delivery planner -> sender -> simulated phone."""
from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal

import pytest

from tests.evals import graders as g
from tests.evals.conftest import RESULTS
from tests.evals.scenarios import SCENARIOS
from tests.evals.shops import SHOPS, build_shop
from tests.world import Recorder, install_llm, settle, sim_texts


async def _context(world, shop, spec, phone, steps, rec) -> g.Context:  # noqa: ANN001
    turns = await world.q(shop, "SELECT decision, cost_micros, latency_ms FROM turns ORDER BY created_at")
    issued: set[Decimal] = set()
    for t in turns:
        for ev in (t["decision"] or {}).get("engine", []):
            for v in ev.get("values", []):
                if v.get("kind") in ("price", "total", "discount_amount", "advance", "saving") or v.get("amount"):
                    issued.add(Decimal(str(v["amount"])))
    prof = {Decimal(m.replace(",", "")) for m in re.findall(r"(?:₹|Rs\.?)\s*([\d,]+)", json.dumps(spec.profile, ensure_ascii=False))}
    cust = await world.q(shop, "SELECT opted_out FROM customers")
    all_text = json.dumps(spec.products, default=str)
    return g.Context(
        shop_key=spec.key, floors=spec.floors, profile_numbers=prof, issued=issued, steps=steps,
        handoffs=await world.q(shop, "SELECT reason, status FROM handoffs"), deals=await world.q(shop, "SELECT kind, status FROM deals"),
        gaps=await world.q(shop, "SELECT question FROM knowledge_gaps"), opted_out=bool(cust and cust[0]["opted_out"]),
        llm_inputs=" ".join(json.dumps(r.input, ensure_ascii=False, default=str) for r in rec.requests), all_text=all_text)


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.id for s in SCENARIOS])
async def test_scenario(world, sc):
    spec = SHOPS[sc.shop]
    shop = await build_shop(world, spec)
    phone = f"+9199{uuid.uuid4().int % 10**8:08d}"
    rec = Recorder(world.rt.agent.llm)
    old = install_llm(world, rec)
    steps: list[g.StepResult] = []
    failures: list[str] = []
    try:
        consumed = 0
        for st in sc.steps:
            await world.customer_says(shop, phone, st.say)
            if not st.checks and not st.wait_silent:
                continue                                                       # a fragment: the next step waits for the answer
            if st.wait_silent:
                await world.drain_for(1.5)
            else:
                await settle(world, shop, phone, want=consumed + 1, timeout=12)
                for _ in range(8):                                             # more parts may follow: wait until the thread is quiet
                    n_before = len(await sim_texts(world, shop, phone))
                    await world.drain_for(0.7)
                    if len(await sim_texts(world, shop, phone)) == n_before:
                        break
            texts = await sim_texts(world, shop, phone)
            res = g.StepResult(st.say, texts[consumed:])
            consumed = len(texts)
            steps.append(res)
        await world.drain_for(0.5)
        ctx = await _context(world, shop, spec, phone, steps, rec)
        for st, res in zip([s for s in sc.steps if s.checks or s.wait_silent], steps, strict=True):
            failures += [f"step {st.say!r}: {m}" for chk in st.checks if (m := chk(ctx, res))]
        failures += [m for fin in sc.final if (m := fin(ctx))]
        failures += [f"[invariant] {m}" for inv in g.INVARIANTS if (m := inv(ctx))]
        turns = await world.q(shop, "SELECT cost_micros, latency_ms, decision->'checks' AS checks, decision->>'intent' AS intent FROM turns ORDER BY created_at")
        RESULTS.append({"id": sc.id, "title": sc.title, "shop": sc.shop, "tags": list(sc.tags), "passed": not failures, "failures": failures,
                        "transcript": [{"customer": s.said, "assistant": s.replies} for s in steps],
                        "turns": len(turns), "intents": [t["intent"] for t in turns], "check_failures": [t["checks"] for t in turns if t["checks"] and any(not a["ok"] for a in t["checks"].get("attempts", []))],
                        "cost_inr": sum((t["cost_micros"] or 0) for t in turns) / 1e6,
                        "latency_ms": int(sum((t["latency_ms"] or 0) for t in turns) / max(1, len(turns)))})
    finally:
        world.rt.agent.llm = old
    assert not failures, f"{sc.id} ({sc.title})\n  " + "\n  ".join(failures) + "\n  transcript: " + json.dumps(
        [{"customer": s.said, "assistant": s.replies} for s in steps], ensure_ascii=False)
