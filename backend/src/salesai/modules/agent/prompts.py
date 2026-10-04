"""Prompts are versioned files; each turn records the prompt versions it used."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    text: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@lru_cache
def load(prompt_id: str, version: int | None = None, directory: Path | None = None) -> Prompt:
    files = sorted((directory or DIR).glob(f"{prompt_id}.v*.md"), key=lambda p: int(re.search(r"\.v(\d+)\.md$", p.name).group(1)))  # type: ignore[union-attr]
    if not files:
        raise FileNotFoundError(prompt_id)
    f = next((p for p in files if version and p.name.endswith(f".v{version}.md")), files[-1])
    raw = f.read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    meta = dict(line.split(": ", 1) for line in (m.group(1).splitlines() if m else []))
    return Prompt(prompt_id, int(meta.get("version", 1)), (m.group(2) if m else raw).strip())
