"""Definition of done: new configuration is documented in .env.example (and every key there is real)."""
import re
from pathlib import Path

from salesai.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
FUTURE: set[str] = set()


def test_every_setting_is_documented_in_env_example():
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.M))
    missing = [n.upper() for n in Settings.model_fields if n.upper() not in documented]
    assert missing == []
    assert documented - {n.upper() for n in Settings.model_fields} <= FUTURE
