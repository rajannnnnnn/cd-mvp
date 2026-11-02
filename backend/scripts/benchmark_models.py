"""Model benchmark (ADR 0034): which model should run each pipeline stage, judged in the founder's order —
security, then answer quality, then latency, then cost.

    python scripts/benchmark_models.py cost         # free, no key: real context sizes from the eval scenarios x each
                                                     # candidate's prices -> projected model cost per conversation
    python scripts/benchmark_models.py run          # shows the plan and the estimated spend, spends nothing
    python scripts/benchmark_models.py run --yes    # costs money: runs the eval suite on every candidate and ranks them

Candidates are sets of LLM_* settings in benchmark/candidates.json. Both modes need a Postgres for the evals
(TEST_PG_SUPERUSER_URL); `run --yes` also needs each vendor's key in the environment. Results go to benchmark/results/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from salesai.modules.agent.llm.base import BUILTIN_PRICES, ModelPrice  # noqa: E402

CANDIDATES = ROOT / "benchmark" / "candidates.json"
OUT = ROOT / "benchmark" / "results"
STAGES = ("planner", "writer", "check")
USD_INR = 85.0
# Rough tokenization: Latin text and JSON ~4 chars/token; Devanagari, Tamil and other Indic scripts ~2.
ASCII_PER_TOKEN, OTHER_PER_TOKEN = 4.0, 2.0
REASONING_TOKENS = 500     # sensitivity only: a reasoning model's thinking per call cannot be measured without a key
CHECK_CALL = (400, 60)     # (in, out) tokens of the optional model check, which never runs under the local stand-in
KEYS = {"openai_compat": "OPENAI_COMPAT_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def _tokens(size: list[int]) -> float:
    chars, other = size
    return (chars - other) / ASCII_PER_TOKEN + other / OTHER_PER_TOKEN


def _route(env: dict[str, str], stage: str) -> tuple[str, str]:
    st = stage if stage in STAGES else "check"                 # owner config-by-chat rides the check route
    provider = env.get(f"LLM_PROVIDER_{st.upper()}") or env.get("LLM_PROVIDER", "local")
    return provider, env.get(f"LLM_MODEL_{st.upper()}", "openai/gpt-oss-20b")


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    return {**env, **extra}


def _evals(env: dict[str, str], *plugins: str) -> int:
    cmd = [sys.executable, "-m", "pytest", "tests/evals", "-q", "-p", "no:cacheprovider"]
    for p in plugins:
        cmd += ["-p", p]
    return subprocess.run(cmd, cwd=ROOT, env=env, check=False).returncode  # noqa: S603 - fixed command, our own interpreter


def measure() -> dict[str, Any]:
    """Run the eval scenarios under the free local stand-in and record the size of every model call."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sizes.json"
        _evals(_clean_env(LLM_PROVIDER="local", SIZES_OUT=str(out)), "tests.evals.ctxsize_plugin")
        if not out.exists():
            sys.exit("measurement failed: the eval run produced no sizes (is TEST_PG_SUPERUSER_URL set and Postgres up?)")
        data: dict[str, Any] = json.loads(out.read_text())
        return data


def project(sizes: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    prices = {**BUILTIN_PRICES, **{m: ModelPrice(*map(float, r)) for m, r in cand.get("prices", {}).items()}}
    env: dict[str, str] = cand["env"]
    missing: set[str] = set()
    tin = cold = warm = think = 0.0
    writers = 0
    for c in sizes["calls"]:
        _, model = _route(env, c["stage"])
        p = prices.get(model)
        if p is None:
            missing.add(model)
            continue
        fixed, vol, out = _tokens(c["system"]) + _tokens(c["stable"]), _tokens(c["volatile"]), _tokens(c["out"])
        tin += fixed + vol
        cold += (fixed + vol) * p.input + out * p.output
        warm += fixed * p.cached_input + vol * p.input + out * p.output      # stable prefix served from cache
        think += REASONING_TOKENS * p.output
        writers += c["stage"] == "writer"
    _, cm = _route(env, "check")
    cp = prices.get(cm)
    if cp is None:
        missing.add(cm)
    else:                                       # one model check per writer attempt on a real model
        cin, cout = CHECK_CALL
        tin += writers * cin
        cold += writers * (cin * cp.input + cout * cp.output)
        warm += writers * (cin * cp.input + cout * cp.output)
        think += writers * REASONING_TOKENS * cp.output
    n = max(1, int(sizes["scenarios"]))
    inr = USD_INR / 1e6 / n
    return {"name": cand["name"], "missing": sorted(missing), "input_tokens": round(tin / n),
            "cold": cold * inr, "warm": warm * inr, "think": think * inr}


def _cost_table(rows: list[dict[str, Any]], n: int, calls: int) -> str:
    lines = [f"Projected **model** cost per conversation, from {calls} model calls across {n} eval conversations",
             "(local stand-in contexts, rough tokenization). Meta's per-message fee is separate (ADR 0026).", "",
             "| Candidate | Input tokens / conv | Cold | Warm (stable prefix cached) | Extra if 500 reasoning tokens / call |",
             "|---|---|---|---|---|"]
    for r in rows:
        if r["missing"]:
            lines.append(f"| {r['name']} | — | price needed: {', '.join(r['missing'])} | — | — |")
        else:
            lines.append(f"| {r['name']} | {r['input_tokens']:,} | ₹{r['cold']:.3f} | ₹{r['warm']:.3f} | +₹{r['think']:.3f} |")
    return "\n".join(lines)


def _write(stem: str, md: str, data: Any) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    path = OUT / f"{stem}.md"
    path.write_text(md + "\n")
    return path


def cmd_cost(cands: list[dict[str, Any]]) -> None:
    sizes = measure()
    rows = [project(sizes, c) for c in cands]
    md = _cost_table(rows, sizes["scenarios"], len(sizes["calls"]))
    print(md)
    print(f"\nwritten to {_write(f'cost-{dt.date.today()}', md, {'sizes': sizes, 'rows': rows})}")


def cmd_run(cands: list[dict[str, Any]], yes: bool) -> None:
    sizes = measure()
    plan = [(c, project(sizes, c)) for c in cands]
    print("Plan (real model calls, real money):")
    for c, p in plan:
        providers = {_route(c["env"], st)[0] for st in STAGES}
        lacks = [KEYS[pr] for pr in sorted(providers) if pr in KEYS and not os.environ.get(KEYS[pr])]
        why = "price needed: " + ", ".join(p["missing"]) if p["missing"] else ("missing " + ", ".join(lacks) if lacks else "")
        est = (p["cold"] + p["think"]) * sizes["scenarios"]
        print(f"  - {c['name']}: " + (f"SKIP ({why})" if why else f"~₹{est:.2f} for {sizes['scenarios']} conversations"))
    if not yes:
        print("\nNothing was spent. Re-run with --yes to run these candidates.")
        sys.exit(2)
    results = []
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    for c, p in plan:
        providers = {_route(c["env"], st)[0] for st in STAGES}
        if p["missing"] or any(pr in KEYS and not os.environ.get(KEYS[pr]) for pr in providers):
            continue
        report = OUT / f"run-{stamp}-{len(results)}.eval.json"
        OUT.mkdir(parents=True, exist_ok=True)
        extra = {"EVAL_REPORT": str(report), "EVAL_TIMEOUT_S": "40", **c["env"]}
        if c.get("prices"):
            extra["LLM_PRICES"] = json.dumps(c["prices"])
        print(f"\n=== {c['name']} ===", flush=True)
        _evals(_clean_env(**extra))
        if report.exists():
            r = json.loads(report.read_text())
            results.append({"name": c["name"], "invariant_failures": r["invariant_failures"], "passed": r["passed"],
                            "scenarios": r["scenarios"], "latency_ms": r["avg_latency_ms"], "llm_failed": r["llm_failed"],
                            "truncated": r["truncated"], "cost_per_conv": r["cost_inr_per_conversation"]})
    # The founder's order: security (no invariant failures), then answer quality, then latency, then cost.
    results.sort(key=lambda r: (r["invariant_failures"], -r["passed"] / max(1, r["scenarios"]), r["latency_ms"], r["cost_per_conv"]))
    lines = ["Ranked by: invariant failures, then scenarios passed, then latency, then cost.", "",
             "| Rank | Candidate | Invariant failures | Passed | Avg latency | Failed calls | Truncated | Cost / conv |",
             "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | {r['name']} | {r['invariant_failures']} | {r['passed']}/{r['scenarios']} | {r['latency_ms']} ms | "
                     f"{r['llm_failed']} | {r['truncated']} | ₹{r['cost_per_conv']:.3f} |")
    md = "\n".join(lines)
    print("\n" + md)
    print(f"\nwritten to {_write(f'run-{stamp}', md, results)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["cost", "run"])
    ap.add_argument("--yes", action="store_true", help="run: actually call the models and spend money")
    ap.add_argument("--candidates", type=Path, default=CANDIDATES)
    a = ap.parse_args()
    cands = json.loads(a.candidates.read_text())["candidates"]
    cmd_cost(cands) if a.mode == "cost" else cmd_run(cands, a.yes)


if __name__ == "__main__":
    main()
