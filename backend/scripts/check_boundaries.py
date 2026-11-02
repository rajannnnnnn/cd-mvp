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
  B9  component separation (ADR 0025): web, gateway and agent never import each other; they meet in the database and
      over a few named links. `shared` is the common library and imports none of them. Existing crossings are listed in
      KNOWN_CROSSINGS: a NEW one fails, and so does a listed one that no longer occurs, so the list only shrinks.

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
    "billing": set(),
}
# api is the HTTP layer: it may use these module interfaces (never the reverse).
API_MAY_USE = {"auth", "billing", "catalog", "channels", "handoffs", "pricing", "sales", "tenants"}

# B2: submodules that are part of a module's public interface (everything else is internal)
PUBLIC_SUBMODULES: dict[str, set[str]] = {
    "agent": {"prompts"},                 # versioned prompt loader, shared with the owner-loop interviewer
    "catalog": {"models", "repo"},
    "pricing": {"engine"},
    "channels": {"base", "ingress"},
    "alerting": set(),
    "billing": set(),
}

# B9: which component each unit belongs to. Foundation units (config, db, obs, events, queue...) are shared.
# Composition roots (runtime, scheduler, __main__, seed) wire components together and belong to none.
COMPONENT_OF_UNIT: dict[str, str] = {
    "api": "web", "modules.auth": "web", "modules.billing": "web",
    "modules.channels": "gateway", "modules.alerting": "gateway",
    "modules.agent": "agent", "modules.conversations": "agent", "modules.notifications": "agent", "modules.delivery": "agent",
    "modules.tenants": "shared", "modules.catalog": "shared", "modules.pricing": "shared", "modules.sales": "shared",
    "modules.handoffs": "shared",
}
# File-level overrides: the unit's default is wrong for these files.
COMPONENT_OF_FILE: dict[str, str] = {
    "api/routes/system.py": "gateway",            # the public webhook endpoints are the gateway's receive part
    "modules/delivery/sender.py": "gateway",      # the only way out to Meta is the gateway's send part
}
# Crossings that exist today and are being removed (ADR 0025 build order). "<importing file> -> <imported module>".
KNOWN_CROSSINGS: frozenset[str] = frozenset({
    "modules/notifications/service.py -> salesai.modules.channels",   # owner alerts: the agent asks the gateway to send them
    "modules/conversations/inbound.py -> salesai.modules.channels",   # the Meta payload parser: moves to the gateway's inbound part
})


def _component(rels: str, unit: str) -> str | None:
    if rels in COMPONENT_OF_FILE:
        return COMPONENT_OF_FILE[rels]
    if unit in COMPOSITION_ROOTS:
        return None
    return COMPONENT_OF_UNIT.get(unit, "shared")


def _target_component(parts: list[str], root: Path) -> tuple[str | None, str]:
    """(component, unit) of an imported `salesai.…` module path."""
    sub = parts[1:]
    if sub[:1] == ["modules"] and len(sub) > 1:
        unit = f"modules.{sub[1]}"
        for n in range(len(sub), 1, -1):                       # most specific file first
            cand = "/".join(sub[:n]) + ".py"
            if cand in COMPONENT_OF_FILE:
                return COMPONENT_OF_FILE[cand], unit
        return _component("", unit), unit
    unit = sub[0] if sub else ""
    for n in range(len(sub), 0, -1):
        cand = "/".join(sub[:n]) + ".py"
        if cand in COMPONENT_OF_FILE:
            return COMPONENT_OF_FILE[cand], unit
    return _component("", unit), unit


# B4: vendor package -> the only files allowed to import it (paths relative to the package root)
VENDOR_ADAPTERS = {
    "anthropic": {"modules/agent/llm/anthropic_provider.py"},
    "httpx": {"modules/channels/whatsapp.py", "modules/channels/embedded_signup.py", "modules/agent/llm/anthropic_provider.py",
              "modules/agent/llm/openai_compat_provider.py", "modules/alerting/webhook_sink.py"},
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


def check(root: Path, baseline: frozenset[str] | None = None) -> list[Violation]:
    """`baseline` is the set of known B9 crossings; None means none are tolerated (and no staleness check)."""
    v: list[Violation] = []
    seen_crossings: set[str] = set()
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
            me_comp = _component(rels, src)                                                   # B9
            if me_comp is not None:
                tgt_comp, tgt_unit = _target_component(parts, root)
                crosses = tgt_comp is not None and tgt_comp != me_comp and (tgt_comp != "shared" or me_comp == "shared")
                if me_comp == "shared" and tgt_comp not in (None, "shared"):
                    crosses = True                                                            # the library imports no component
                if crosses and tgt_unit != src:
                    key = f"{rels} -> {name}"
                    seen_crossings.add(key)
                    if baseline is None or key not in baseline:
                        v.append(Violation("B9", rels, line, f"{me_comp} may not import {tgt_comp} code ('{name}'); components meet in the database or over a named link (ADR 0025)"))
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
    if baseline is not None:
        for key in sorted(baseline - seen_crossings):                                         # B9: the ratchet only tightens
            v.append(Violation("B9", key.split(" -> ")[0], 1, f"known crossing no longer exists; remove it from KNOWN_CROSSINGS: {key}"))
    return v


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1] / "src" / "salesai"
    violations = check(root, KNOWN_CROSSINGS)
    for x in violations:
        print(x)
    print(f"boundary check: {'FAILED, ' + str(len(violations)) + ' violation(s)' if violations else 'ok'}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
