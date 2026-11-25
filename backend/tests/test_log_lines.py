"""Logs are for debugging at a glance: one plain line when a part starts (what it is, where it listens, what it talks to) and one plain line per
decision (what happened to a customer's message). Never a password, key or phone number, and never what a customer wrote."""
from __future__ import annotations

from types import SimpleNamespace

from salesai.logsummary import startup_line, turn_line

SETTINGS = SimpleNamespace(role="worker", env="production", http_port=8000, queue_backend="postgres", queues=["turns", "outbound"], uses_model=True,
                           database_url="postgresql://app_user:S3cretPassw0rd@db.example.com:5432/salesai?sslmode=require",
                           llm_provider="openai_compat", llm_model_planner="openai/gpt-oss-20b", llm_model_writer="openai/gpt-oss-20b",
                           llm_model_check="openai/gpt-oss-20b", openai_compat_api_key="sk-or-v1-supersecretkey1234567890")


def test_the_start_up_line_says_what_where_and_with_what_and_never_shows_a_secret():
    line = startup_line(SETTINGS, "worker")
    assert "worker" in line and "production" in line and ":8000" in line
    assert "db.example.com:5432/salesai" in line and "app_user" in line and "encrypted" in line and "NOT" not in line
    assert "openai_compat" in line and "gpt-oss-20b" in line and "turns" in line
    assert "S3cretPassw0rd" not in line and "sk-or-v1" not in line and "supersecret" not in line


def test_a_database_without_tls_is_said_plainly():
    s = SimpleNamespace(**{**SETTINGS.__dict__, "database_url": "postgresql://app_user:pw@host.docker.internal:5432/salesai"})
    assert "NOT encrypted" in startup_line(s, "worker")


def test_a_part_that_never_calls_a_model_does_not_talk_about_one():
    s = SimpleNamespace(**{**SETTINGS.__dict__, "role": "api", "uses_model": False})
    assert "model" not in startup_line(s, "api").lower()


def outcome(**kw):
    base = dict(status="committed", intent="price_query", reason=None, parts=["a", "b"], reaction_only=False,
                llm={"calls": 3, "failed": 0, "cost_micros": 12_000, "llm_ms": 2600,
                     "by_stage": {"planner": {"calls": 1, "ms": 1200}, "writer": {"calls": 1, "ms": 900}, "check": {"calls": 1, "ms": 500}}})
    return SimpleNamespace(**{**base, **kw})


def test_one_line_says_what_happened_to_the_message_and_what_it_cost():
    line = turn_line(outcome(), eot_reason="quiet_unsure", waited_ms=2700)
    for part in ("replied", "price_query", "2 messages", "3 model calls", "planner 1.2s", "writer 0.9s", "check 0.5s", "2.6s", "₹0.012", "waited 2.7s", "quiet_unsure"):
        assert part in line, (part, line)


def test_a_discarded_turn_says_why_and_that_the_work_was_wasted():
    line = turn_line(outcome(status="superseded", reason="the customer wrote again while the AI was planning; stopped before writing", parts=[],
                             llm={"calls": 1, "failed": 0, "cost_micros": 3000, "llm_ms": 1100, "by_stage": {"planner": {"calls": 1, "ms": 1100}}}),
                     eot_reason="quiet_complete", waited_ms=1300)
    assert "discarded" in line and "wrote again" in line and "₹0.003" in line


def test_a_turn_with_no_model_call_says_so_and_a_failed_call_is_counted():
    assert "no model call" in turn_line(outcome(llm={"calls": 0, "failed": 0, "cost_micros": 0, "llm_ms": 0, "by_stage": {}}), None, None)
    assert "1 failed" in turn_line(outcome(llm={"calls": 2, "failed": 1, "cost_micros": 0, "llm_ms": 0, "by_stage": {}}), None, None)


def test_the_line_never_holds_what_the_customer_wrote():
    line = turn_line(outcome(parts=["Your saree is 4500 rupees, call 9876543210"]), "quiet", 1000)
    assert "4500" not in line and "9876543210" not in line
