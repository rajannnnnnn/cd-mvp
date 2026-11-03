"""ADR 0040: there are no daily summaries (founder decision; supersedes FR-RP-1). Nothing in the running product schedules, builds,
sends or offers one; the owner's numbers live in the app's analytics, and alerts for hand-offs and deals are immediate."""
from __future__ import annotations

import re
from pathlib import Path

import salesai
from salesai import templates
from salesai.modules.tenants import defaults
from salesai.scheduler import DUTIES, Scheduler
from tests.test_api import V1, hdr, owner

ROOT = Path(salesai.__file__).resolve().parents[3]


def test_the_scheduler_has_no_summary_duty():
    assert not any("summar" in d for duties in DUTIES.values() for d in duties)
    assert not hasattr(Scheduler, "tick_daily_summaries")


def test_the_owner_loop_cannot_build_or_send_one(world):
    assert not hasattr(world.rt.owner, "daily_summary")
    assert world.rt.registry.get("owner.notifications", "daily_summary") is None


def test_there_is_no_template_and_no_setting_for_it():
    assert "daily_summary" not in templates.TEMPLATES
    assert "daily_summary_hour" not in defaults.CONVERSATION_SETTINGS


async def test_the_api_no_longer_accepts_the_setting(world, client):
    shop, tok = await owner(world, client, "No Summary Shop")
    r = await client.patch(f"{V1}/business", headers=hdr(tok), json={"conversation_settings": {"daily_summary_hour": 20}})
    assert r.status_code == 422


def test_nothing_the_customer_or_owner_reads_promises_one():
    """Source, front end and deployment files (history in migrations and decision records is left as it was)."""
    pat = re.compile(r"daily[ _-]?summar", re.I)
    hits = []
    for base in ("backend/src", "frontend/src", "deploy"):
        for f in (ROOT / base).rglob("*"):
            if f.suffix in (".py", ".ts", ".tsx", ".json", ".yml", ".yaml", ".md", ".example") and f.name not in ("schema.d.ts",) and "node_modules" not in f.parts:
                for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                    if pat.search(line):
                        hits.append(f"{f.relative_to(ROOT)}:{i}")
    assert hits == []
