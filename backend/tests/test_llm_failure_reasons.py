"""A model failure reaches the founder with a specific reason and the right urgency, and never with customer text."""
import pytest

from salesai.modules.agent.llm.failure import alert_message, classify

CASES = [
    ("openai_compat: api 401: Invalid API key", "key_rejected", "critical"),
    ("openai_compat: api 402: Insufficient credits", "out_of_credit", "critical"),
    ("openai_compat: api 404: No endpoints found for openai/gpt-oss-99b", "model_not_found", "critical"),
    ("openai_compat: circuit open", "no_provider", "critical"),
    ("no providers configured", "no_provider", "critical"),
    ("openai_compat: api 429: rate limit exceeded", "rate_limited", "high"),
    ("openai_compat: api 503: upstream overloaded", "provider_error", "high"),
    ("openai_compat: connection: ReadTimeout: ", "connection", "high"),
    ("openai_compat: reply truncated at max_tokens=1200 for stage planner (model used 1200 tokens, effort medium)", "truncated", "high"),
    ("openai_compat: reply contained no JSON object", "bad_output", "medium"),
    ("openai_compat: reply failed the PlannerOutput schema: 2 error(s)", "bad_output", "medium"),
    ("something nobody has seen before", "unknown", "high"),
]


@pytest.mark.parametrize(("error", "reason", "severity"), CASES)
def test_each_failure_has_its_own_reason_and_urgency(error, reason, severity):
    f = classify(error)
    assert (f.reason, f.severity) == (reason, severity)
    assert f.summary and f.fix


def test_a_failure_everyone_hits_is_critical_and_a_format_slip_is_not():
    assert classify("api 401: x").severity == "critical"
    assert classify("reply contained no JSON object").severity == "medium"


def test_the_message_names_the_step_the_reason_and_the_fix_and_is_one_short_line():
    msg = alert_message(classify("api 402: Insufficient credits"), "planner", "api 402: Insufficient credits\n" + "x" * 1000)
    assert "planner" in msg and "out_of_credit" in msg and "Fix:" in msg
    assert "\n" not in msg and len(msg) < 700
