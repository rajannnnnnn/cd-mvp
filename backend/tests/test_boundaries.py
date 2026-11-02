"""The architecture checker itself is tested: the real tree passes, and each rule catches a synthetic violation."""
import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("check_boundaries", Path(__file__).resolve().parents[1] / "scripts" / "check_boundaries.py")
cb = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["check_boundaries"] = cb
_spec.loader.exec_module(cb)  # type: ignore[union-attr]

SRC = Path(__file__).resolve().parents[1] / "src" / "salesai"


def test_real_tree_has_no_violations():
    assert [str(x) for x in cb.check(SRC, cb.KNOWN_CROSSINGS)] == []


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))
    return tmp_path


def rules(tmp_path: Path, files: dict[str, str]) -> set[str]:
    return {x.rule for x in cb.check(_tree(tmp_path, files))}


def test_b1_undeclared_module_dependency(tmp_path):
    assert "B1" in rules(tmp_path, {"modules/pricing/__init__.py": "from salesai.modules.agent import X\n", "modules/agent/__init__.py": "X = 1\n"})


def test_b2_internal_import(tmp_path):
    files = {"modules/agent/__init__.py": "", "modules/agent/pipeline.py": "", "modules/conversations/__init__.py": "from salesai.modules.agent.pipeline import AgentService\n"}
    assert "B2" in rules(tmp_path, files)
    assert "B2" in rules(tmp_path / "b", {**files, "modules/conversations/__init__.py": "from salesai.modules.agent import pipeline\n"})


def test_public_submodule_is_allowed(tmp_path):
    files = {"modules/catalog/__init__.py": "", "modules/catalog/repo.py": "", "api/x.py": "from salesai.modules.catalog import repo\nfrom salesai.modules.catalog.repo import f\n"}
    assert not rules(tmp_path, files)


def test_b3_conversation_code_must_not_import_provider_wire_formats(tmp_path):
    files = {"modules/channels/__init__.py": "", "modules/channels/whatsapp.py": "", "modules/conversations/__init__.py": "from salesai.modules.channels.whatsapp import parse_webhook\n"}
    assert "B3" in rules(tmp_path, files)


def test_b4_vendor_sdk_outside_adapter(tmp_path):
    assert "B4" in rules(tmp_path, {"modules/agent/pipeline.py": "import anthropic\n"})
    assert not rules(tmp_path / "ok", {"modules/agent/llm/anthropic_provider.py": "import anthropic\nimport httpx\n"})


def test_b5_queue_backend_named_outside_composition_root(tmp_path):
    assert "B5" in rules(tmp_path, {"modules/handoffs/service.py": "from salesai.queue.postgres import PostgresQueue\n"})
    assert not rules(tmp_path / "ok", {"runtime.py": "from salesai.queue.postgres import PostgresQueue\n"})


def test_b6_pricing_engine_must_stay_pure(tmp_path):
    assert "B6" in rules(tmp_path, {"modules/pricing/engine.py": "import psycopg\n"})
    assert "B6" in rules(tmp_path / "b", {"modules/pricing/engine.py": "from salesai.db import x\n"})
    assert not rules(tmp_path / "ok", {"modules/pricing/engine.py": "from __future__ import annotations\nfrom decimal import Decimal\n"})


@pytest.mark.parametrize("token", ["price_floors", "pricing_tenant"])
def test_b7_floor_access_outside_allow_list(tmp_path, token):
    assert "B7" in rules(tmp_path, {"modules/agent/pipeline.py": f"x = '{token}'\n"})
    assert not rules(tmp_path / "ok", {"modules/pricing/service.py": f"x = '{token}'\n"})


def test_b8_dependencies_point_inward(tmp_path):
    assert "B8" in rules(tmp_path, {"modules/sales/service.py": "from salesai.runtime import Runtime\n"})
    assert "B8" in rules(tmp_path / "b", {"modules/sales/service.py": "from salesai.api import app\n"})


# ---------------------------------------------------------------- B9: component separation (ADR 0025)

def test_b9_web_may_not_import_agent_code(tmp_path):
    assert "B9" in rules(tmp_path, {"api/routes/x.py": "from salesai.modules.conversations import Y\n", "modules/conversations/__init__.py": "Y = 1\n"})


def test_b9_agent_may_not_import_web_code(tmp_path):
    assert "B9" in rules(tmp_path, {"modules/agent/__init__.py": "from salesai.modules.billing import Y\n", "modules/billing/__init__.py": "Y = 1\n"})


def test_b9_gateway_may_not_import_agent_code(tmp_path):
    assert "B9" in rules(tmp_path, {"modules/channels/__init__.py": "from salesai.modules.agent import Y\n", "modules/agent/__init__.py": "Y = 1\n"})


def test_b9_the_shared_library_imports_no_component(tmp_path):
    assert "B9" in rules(tmp_path, {"modules/catalog/__init__.py": "from salesai.modules.channels import Y\n", "modules/channels/__init__.py": "Y = 1\n"})


def test_b9_every_component_may_use_the_shared_library(tmp_path):
    files = {"modules/handoffs/__init__.py": "Y = 1\n", "api/routes/x.py": "from salesai.modules.handoffs import Y\n",
             "modules/agent/__init__.py": "from salesai.modules.handoffs import Y\n", "modules/channels/__init__.py": "from salesai.modules.handoffs import Y\n"}
    assert "B9" not in rules(tmp_path, files)


def test_b9_a_file_override_moves_one_file_to_another_component(tmp_path):
    # the public webhook routes live in the API layer but are the gateway's: a web file may not use them, a gateway file may
    files = {"api/routes/system.py": "Y = 1\n", "modules/channels/__init__.py": "from salesai.api.routes.system import Y\n"}
    assert "B9" not in rules(tmp_path / "gw", files)
    assert "B9" in rules(tmp_path / "ag", {"modules/agent/__init__.py": "from salesai.api.routes.system import Y\n", "api/routes/system.py": "Y = 1\n"})


def test_b9_a_known_crossing_is_tolerated_but_a_new_one_is_not(tmp_path):
    files = {"modules/agent/__init__.py": "from salesai.modules.channels import Y\n", "modules/channels/__init__.py": "Y = 1\n"}
    tree = _tree(tmp_path, files)
    known = frozenset({"modules/agent/__init__.py -> salesai.modules.channels"})
    assert [x for x in cb.check(tree, known) if x.rule == "B9"] == []
    assert [x for x in cb.check(tree, frozenset()) if x.rule == "B9"]


def test_b9_a_listed_crossing_that_no_longer_exists_fails_so_the_list_only_shrinks(tmp_path):
    tree = _tree(tmp_path, {"modules/agent/__init__.py": "X = 1\n"})
    stale = frozenset({"modules/agent/__init__.py -> salesai.modules.channels"})
    out = [x for x in cb.check(tree, stale) if x.rule == "B9"]
    assert out and "no longer exists" in out[0].message


def test_b9_the_baseline_holds_only_real_crossings_between_components():
    for key in cb.KNOWN_CROSSINGS:
        src, _, tgt = key.partition(" -> ")
        assert (SRC / src).exists(), key
        assert tgt.startswith("salesai."), key
