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
    assert [str(x) for x in cb.check(SRC)] == []


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
