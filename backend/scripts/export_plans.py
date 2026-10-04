"""Writes the plan catalogue to the frontend (pricing page and billing screen). A test fails when the committed file is stale.
Usage: python scripts/export_plans.py"""
from __future__ import annotations

import json
from pathlib import Path

from salesai.modules.billing import catalogue

OUT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "site" / "plans.json"

if __name__ == "__main__":
    OUT.write_text(json.dumps(catalogue(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")
