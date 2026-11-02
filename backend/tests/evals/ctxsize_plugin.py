"""Benchmark helper (ADR 0034), loaded only by scripts/benchmark_models.py with `-p tests.evals.ctxsize_plugin`.
Records the rendered size of every model request and answer the evals make under the local stand-in, so model cost
per conversation can be projected for any candidate from real conversation contexts rather than assumed ones.
Only character counts are kept, never the text."""
from __future__ import annotations

import json
import os
from typing import Any

from salesai.modules.agent.llm import local

SIZES: list[dict[str, Any]] = []
_orig = local.LocalRulesProvider.generate


def _size(s: str) -> list[int]:
    return [len(s), sum(1 for ch in s if ord(ch) > 127)]       # [chars, non-ASCII chars]: Indic scripts tokenize denser


async def _generate(self, req, schema):  # type: ignore[no-untyped-def]
    res = await _orig(self, req, schema)
    stable, volatile = req.render_parts() if req.cache_keys else ("{}", req.render_user_content())
    SIZES.append({"stage": req.stage, "system": _size(req.system), "stable": _size(stable) if stable != "{}" else [0, 0],
                  "volatile": _size(volatile), "out": _size(json.dumps(res.output.model_dump(), ensure_ascii=False, default=str))})
    return res


local.LocalRulesProvider.generate = _generate  # type: ignore[method-assign]


def pytest_sessionfinish(session, exitstatus):  # type: ignore[no-untyped-def]
    from tests.evals.conftest import RESULTS
    path = os.environ.get("SIZES_OUT")
    if path:
        with open(path, "w") as f:
            json.dump({"scenarios": len(RESULTS), "calls": SIZES}, f)
