"""Channel gateway (ADR 0020 follow-up 3, INV-11): only the send part reads WhatsApp access tokens. The agent's context
loader and the turn planner read the columns they need and never `access_token_enc`."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from salesai.modules.agent import context

SRC = Path(__file__).resolve().parents[1] / "src" / "salesai"
# Code that runs in the agent / conversation path. The token belongs to delivery (send) and the channel registry only.
AGENT_PATH = [SRC / "modules" / "agent", SRC / "modules" / "conversations"]


def _py_files():
    for root in AGENT_PATH:
        yield from root.rglob("*.py")


def test_agent_path_never_names_the_token_column():
    hits = [str(p.relative_to(SRC)) for p in _py_files() if "access_token" in p.read_text(encoding="utf-8")]
    assert hits == []


def test_agent_path_never_selects_every_column_of_whatsapp_numbers():
    pat = re.compile(r"SELECT\s+\*\s+FROM\s+whatsapp_numbers", re.IGNORECASE)
    hits = [str(p.relative_to(SRC)) for p in _py_files() if pat.search(p.read_text(encoding="utf-8"))]
    assert hits == []


async def test_loaded_turn_context_carries_no_token(world):
    shop = await world.make_shop()
    await world.customer_says(shop, "+919800002001", "hello")
    await world.drain(["inbound.events"])
    [conv] = await world.q(shop, "SELECT id FROM conversations")
    ctx = await context.load(world.rt.db, shop.business_id, conv["id"], datetime.now(UTC))
    assert not any("token" in k for k in ctx.number)
    assert ctx.number["status"] == "connected"          # what the AI-allowed check needs is still there
