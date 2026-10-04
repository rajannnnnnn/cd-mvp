"""The concession schedule is also implemented in the marketing page (a live demo). This golden file ties the two
together: the backend engine must reproduce it, and frontend/src/site/ladder.test.ts checks the JS against it."""
import json
from decimal import Decimal
from pathlib import Path

from salesai.modules.pricing.engine import concession_levels

GOLDEN = Path(__file__).parent / "golden" / "ladder.json"


def test_engine_reproduces_golden_ladders():
    cases = json.loads(GOLDEN.read_text())
    assert len(cases) >= 100
    for c in cases:
        got = concession_levels(Decimal(c["list"]), Decimal(c["floor"]), c["steps"], Decimal(c["round_to"]))
        assert [int(x) for x in got] == c["levels"], c
