"""Architecture boundary checker (CI gate). Zero dependencies; parses imports with `ast`.

The rules are the code form of docs/TECHNICAL_DESIGN.md ("Boundaries enforced automatically in CI"):

  B1  module dependency graph: a module may import only the modules declared in ALLOWED_DEPS (no cycles by
      construction: the table is a DAG). A new edge needs a deliberate edit here, i.e. a review.
  B2  public interface: other modules reach a module only through its package root, or the submodules listed
      in PUBLIC_SUBMODULES. Composition roots (runtime, seed, scheduler, __main__) may wire anything.
  B3  channel-agnostic core: only the channels module and composition roots may import provider-specific
      channel code (whatsapp, simulator). Conversation and sales logic never see wire formats.
  B4  vendor isolation: each vendor SDK is imported by exactly one adapter file.
  B5  queue seam: producers and consumers use salesai.queue.base; only the composition root names a backend.
  B6  pure pricing: the price engine imports only the standard library (no I/O, no clock, no database).
  B7  floor secrecy (INV-1): code that touches the floor table or the pricing connection pool is limited to an
      allow-list of files.
  B8  no module imports the frontend, API layer or composition roots (dependencies point inward).

Usage: python scripts/check_boundaries.py [src/salesai]      exit code 1 on any violation."""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

COMPOSITION_ROOTS = {"runtime", "seed", "scheduler", "__main__"}
FOUNDATION = {"config", "db", "obs", "phone", "rules", "templates", "crypto", "ratelimit", "migrate", "events", "queue"}

# B1: module -> modules it may import (besides FOUNDATION). Keep this table equal to the architecture diagram.
ALLOWED_DEPS: dict[str, set[str]] = {
    "tenants": set(),
    "catalog": set(),
    "pricing": set(),
    "sales": set(),
    "handoffs": set(),
    "channels": {"tenants"},
    "auth": {"channels"},
    "delivery": {"channels", "handoffs"},
    "agent": {"catalog", "delivery", "handoffs", "pricing", "sales"},
    "conversations": {"agent", "channels", "handoffs"},
    "notifications": {"agent", "catalog", "channels", "handoffs", "sales"},
    "alerting": {"channels"},
}
# api is the HTTP layer: it may use these module interfaces (never the reverse).
API_MAY_USE = {"auth", "catalog", "channels", "handoffs", "pricing", "sales", "tenants"}

# B2: submodules that are part of a module's public interface (everything else is internal)
PUBLIC_SUBMODULES: dict[str, set[str]] = {
    "agent": {"prompts"},                 # versioned prompt loader, shared with the owner-loop interviewer
    "catalog": {"models", "repo"},
    "pricing": {"engine"},
    "channels": {"base", "ingress"},
    "alerting": set(),
}

# B4: vendor package -> the only files allowed to import it (paths relative to the package root)
VENDOR_ADAPTERS = {
    "anthropic": {"modules/agent/llm/anthropic_provider.py"},
    "httpx": {"modules/channels/whatsapp.py", "modules/channels/embedded_signup.py", "modules/agent/llm/anthropic_provider.py", "modules/alerting/webhook_sink.py"},
    "redis": {"queue/redis_backend.py"},
}
# B5
QUEUE_BACKENDS = {"salesai.queue.postgres", "salesai.queue.redis_backend"}
# B6
STDLIB_ONLY_FILES = {"modules/pricing/engine.py"}
# B7
FLOOR_TOKENS = ("price_floors", "pricing_tenant", "pricing_pool", "PRICING_DATABASE_URL", "pricing_database_url")
FLOOR_ALLOWED = {
    "db.py", "config.py", "modules/pricing/service.py", "modules/catalog/repo.py", "runtime.py", "seed.py",
    "modules/tenants/lifecycle.py",   # hard delete and export (the export explicitly omits floors)
}


@dataclass(frozen=True)
class Violation:
    rule: str
    file: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: [{self.rule}] {self.message}"


def _unit(rel: Path) -> str:
    """The architectural unit a file belongs to: 'modules.<name>', a top-level package, or a top-level file."""
    p = rel.parts
    if p[0] == "modules" and len(p) > 2:
        return f"modules.{p[1]}"
    return p[0].removesuffix(".py")


def _imports(tree: ast.AST) -> list[tuple[str, int, list[str]]]:
    out: list[tuple[str, int, list[str]]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(a.name, n.lineno, []) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.append((n.module, n.lineno, [a.name for a in n.names]))
    return out


def check(root: Path) -> list[Violation]:
    v: list[Violation] = []
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root)
        rels = rel.as_posix()
        src = _unit(rel)
        text = f.read_text()
        tree = ast.parse(text)

        for tok in FLOOR_TOKENS:                                                             # B7
            if tok in text and rels not in FLOOR_ALLOWED:
                line = next((i for i, ln in enumerate(text.splitlines(), 1) if tok in ln), 1)
                v.append(Violation("B7", rels, line, f"'{tok}' is reserved for the pricing path (INV-1); allow-list: {sorted(FLOOR_ALLOWED)}"))

        for name, line, names in _imports(tree):
            top = name.split(".")[0]
            if top in VENDOR_ADAPTERS and rels not in VENDOR_ADAPTERS[top]:                   # B4
                v.append(Violation("B4", rels, line, f"'{top}' may only be imported by {sorted(VENDOR_ADAPTERS[top])}"))
            if rels in STDLIB_ONLY_FILES and top != "__future__" and top not in sys.stdlib_module_names:   # B6
                v.append(Violation("B6", rels, line, f"the pricing engine must stay pure; it imports '{name}'"))
            if top != "salesai":
                continue
            parts = name.split(".")
            if name in QUEUE_BACKENDS and src not in COMPOSITION_ROOTS:                      # B5
                v.append(Violation("B5", rels, line, f"only the composition root may name a queue backend ('{name}')"))
            if parts[:2] == ["salesai", "modules"] and len(parts) > 2:
                tgt = parts[2]
                sub = parts[3] if len(parts) > 3 else None
                # `from salesai.modules.x import repo` also reaches a submodule
                reached = {sub} if sub else {n for n in names if (root / "modules" / tgt / f"{n}.py").exists() or (root / "modules" / tgt / n).is_dir()}
                if src == f"modules.{tgt}" or src in COMPOSITION_ROOTS:
                    continue
                if src.startswith("modules."):                                               # B1
                    me = src.split(".")[1]
                    if tgt not in ALLOWED_DEPS.get(me, set()):
                        v.append(Violation("B1", rels, line, f"module '{me}' may not depend on '{tgt}' (allowed: {sorted(ALLOWED_DEPS.get(me, set()))})"))
                elif src == "api":
                    if tgt not in API_MAY_USE:
                        v.append(Violation("B1", rels, line, f"the API layer may not use module '{tgt}'"))
                for r in reached - PUBLIC_SUBMODULES.get(tgt, set()):                        # B2
                    v.append(Violation("B2", rels, line, f"'{tgt}.{r}' is internal to the {tgt} module; import it from 'salesai.modules.{tgt}' or add it to PUBLIC_SUBMODULES"))
                if tgt == "channels" and sub in {"whatsapp", "simulator"}:                   # B3
                    v.append(Violation("B3", rels, line, f"provider-specific channel code ('{name}') may not be used outside the channels module"))
            inward_only = src.startswith("modules.") or src in {"events", "queue", "db", "config", "obs", "phone", "rules"}
            if inward_only and (parts[:2] == ["salesai", "api"] or (len(parts) > 1 and parts[1] in COMPOSITION_ROOTS)):   # B8
                v.append(Violation("B8", rels, line, f"'{name}' points outward; dependencies must point inward"))
    return v


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1] / "src" / "salesai"
    violations = check(root)
    for x in violations:
        print(x)
    print(f"boundary check: {'FAILED, ' + str(len(violations)) + ' violation(s)' if violations else 'ok'}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
