"""`python -m salesai doctor`: the check the founder runs after filling in the configuration, before and after starting the stack.
Each check says what it found and what to do about it; a failed one makes the command exit non-zero. Nothing it does changes data."""
from __future__ import annotations

from salesai.config import Settings
from salesai.doctor import Check, render, run_checks


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


async def test_a_working_setup_passes_the_database_and_separation_checks(rt, pg_urls):
    checks = by_name(await run_checks(rt.settings))
    assert checks["database"].status == "ok", checks["database"]
    assert checks["migrations"].status == "ok", checks["migrations"]
    assert checks["separation"].status == "ok", checks["separation"]


async def test_the_stand_in_model_is_reported_as_not_a_real_model(rt):
    c = by_name(await run_checks(rt.settings))["model"]
    assert c.status == "skip" and "stand-in" in c.detail


async def test_optional_services_that_are_not_configured_are_skipped_with_a_hint(rt):
    checks = by_name(await run_checks(rt.settings))
    assert checks["email"].status == "skip" and "SMTP_HOST" in checks["email"].hint
    assert checks["meta"].status == "skip"


async def test_an_unreachable_database_fails_with_what_to_check(rt):
    broken = rt.settings.model_copy(update={"database_url": "postgresql://nobody:x@127.0.0.1:1/none"})
    c = by_name(await run_checks(broken))["database"]
    assert c.status == "fail" and "DATABASE_URL" in c.hint


async def test_a_model_key_that_does_not_work_fails_and_says_which_step(rt, monkeypatch):
    from salesai.modules.agent.llm import factory
    class Boom:
        async def generate(self, req, schema):
            raise RuntimeError("401 invalid api key")
    monkeypatch.setattr(factory, "make_llm", lambda s: factory.StageRouter({}, default=Boom()))
    s = rt.settings.model_copy(update={"llm_provider": "openai_compat"})
    c = by_name(await run_checks(s))["model"]
    assert c.status == "fail" and "401" in c.detail and "LLM_PROVIDER" in c.hint


def test_render_lists_every_check_and_the_exit_code_follows_failures():
    cs = [Check("a", "ok", "fine"), Check("b", "skip", "not set", "set X"), Check("c", "fail", "broken", "fix Y")]
    text, code = render(cs)
    assert "a" in text and "set X" in text and "fix Y" in text and code == 1
    assert render(cs[:2])[1] == 0


def test_the_command_is_wired_and_documented():
    import pathlib
    main = (pathlib.Path(__file__).parents[1] / "src" / "salesai" / "__main__.py").read_text()
    assert '"doctor"' in main
    assert "doctor" in (pathlib.Path(__file__).parents[2] / "docs" / "GO_LIVE_CHECKLIST.md").read_text()
    assert Settings  # imported: the command reads the same settings the services do
