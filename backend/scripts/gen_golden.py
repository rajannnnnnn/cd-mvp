"""Regenerates tests/golden/ladder.json from the real pricing engine. The marketing page's price-ladder demo
(frontend/src/site/ladder.ts) must produce the same levels; both sides are tested against this file."""
import json
import pathlib
import random
from decimal import Decimal

from salesai.modules.pricing.engine import concession_levels

rng = random.Random(20261004)
cases = []
for _ in range(300):
    round_to = rng.choice([1, 5, 10, 50, 100])
    floor = rng.randrange(100, 30000, 10)
    list_price = floor + rng.choice([0, 10, 40, 150, 400, 1000, 2500, 9000])
    steps = rng.randint(1, 5)
    levels = concession_levels(Decimal(list_price), Decimal(floor), steps, Decimal(round_to))
    cases.append({"list": list_price, "floor": floor, "steps": steps, "round_to": round_to, "levels": [int(x) for x in levels]})
out = pathlib.Path(__file__).resolve().parents[1] / "tests" / "golden" / "ladder.json"
out.write_text("[\n" + ",\n".join(json.dumps(c) for c in cases) + "\n]\n")
print(f"wrote {len(cases)} cases to {out}")
