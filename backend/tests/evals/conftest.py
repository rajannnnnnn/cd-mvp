"""Collects per-scenario results and writes a JSON report when EVAL_REPORT is set.

Run the suite with the deterministic stand-in (default) or a real model:
    pytest tests/evals -q                                    # baseline with the local stand-in
    LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... EVAL_REPORT=evals-sonnet.json pytest tests/evals -q   # costs money
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

RESULTS: list[dict] = []


@pytest.fixture(scope="session", autouse=True)
def _write_report():
    yield
    path = os.environ.get("EVAL_REPORT")
    if path and RESULTS:
        passed = sum(1 for r in RESULTS if r["passed"])
        default = os.environ.get("LLM_PROVIDER", "local")
        route = {st: {"provider": os.environ.get(f"LLM_PROVIDER_{st.upper()}") or default,
                      "model": os.environ.get(f"LLM_MODEL_{st.upper()}", "openai/gpt-oss-20b"),
                      "effort": os.environ.get(f"LLM_EFFORT_{st.upper()}", "low" if st == "check" else "medium")}
                 for st in ("planner", "writer", "check")}
        report = {"provider": default, "route": route, "scenarios": len(RESULTS), "passed": passed,
                  "invariant_failures": sum(1 for r in RESULTS for f in r["failures"] if f.startswith("[invariant]")),
                  "llm_failed": sum(r.get("llm_failed", 0) for r in RESULTS),
                  "truncated": sum(r.get("truncated", 0) for r in RESULTS),
                  "cost_inr_total": round(sum(r["cost_inr"] for r in RESULTS), 4),
                  "cost_inr_per_conversation": round(sum(r["cost_inr"] for r in RESULTS) / len(RESULTS), 4),
                  "avg_latency_ms": round(sum(r["latency_ms"] for r in RESULTS) / len(RESULTS)), "results": RESULTS}
        Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False))
