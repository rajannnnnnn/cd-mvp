"""Why a model call failed, in words the founder can act on, and how loud the alert should be. Pure: it reads only the provider's error text,
never what a customer said, so the result is safe to put in an alert and an email."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMFailure:
    reason: str            # short stable name; with the stage it is the alert's dedup key, so one cause is one alert
    severity: str          # critical: every reply fails until someone acts. high: most replies fail or are slow. medium: some replies
    summary: str           # what happened
    fix: str               # what to do about it


_RULES: tuple[tuple[str, re.Pattern[str], str, str, str], ...] = (
    ("key_rejected", re.compile(r"api (401|403)|invalid api key|incorrect api key|unauthori[sz]ed|forbidden", re.I), "critical",
     "The model provider rejected the API key.", "Check the model key in the agent's secrets (OPENAI_COMPAT_API_KEY) and that it is active and allowed for this model."),
    ("out_of_credit", re.compile(r"api 402|insufficient (credit|fund|balance)|quota|billing|payment required|credit", re.I), "critical",
     "The model account is out of credit or over its spending limit.", "Top up or raise the limit at the provider. Replies stay on the safe holding message until then."),
    ("model_not_found", re.compile(r"api 404|model.{0,40}(not found|does not exist|unavailable|not available)|no endpoints found", re.I), "critical",
     "The provider does not serve the configured model.", "Check LLM_MODEL_PLANNER, LLM_MODEL_WRITER and LLM_MODEL_CHECK against the provider's model list."),
    ("no_provider", re.compile(r"no providers configured|circuit open", re.I), "critical",
     "No model provider is available (none configured, or every one is shut off after repeated failures).", "Check the provider settings and its status page. It retries on its own after a short pause."),
    ("rate_limited", re.compile(r"api 429|rate.?limit|too many requests", re.I), "high",
     "The provider is limiting how fast calls may be made.", "Wait, raise the provider's limit, or add a fallback provider (LLM_FALLBACK_PROVIDER)."),
    ("provider_error", re.compile(r"api 5\d\d|bad gateway|overloaded|service unavailable|internal server error", re.I), "high",
     "The model provider had an error on its side.", "Usually passes by itself. If it lasts, check the provider's status page or add a fallback provider."),
    ("connection", re.compile(r"connection:|timed? ?out|timeout|connect(ion)? (error|refused|reset)|name or service not known", re.I), "high",
     "The model provider could not be reached or was too slow.", "Check this server's internet access and the provider's status. LLM_TIMEOUT_S sets how long a call may take."),
    ("truncated", re.compile(r"truncated at max_tokens", re.I), "high",
     "The model ran out of its token allowance while thinking, so the reply was cut off.", "Raise OPENAI_COMPAT_THINKING_TOKENS or lower the step's LLM_EFFORT_* setting."),
    ("bad_output", re.compile(r"no json object|failed the \w+ schema|no parsed output|model refused", re.I), "medium",
     "The model's answer was not in the expected format.", "A few of these are normal. Many mean the model or its JSON mode setting (LLM_JSON_MODE) does not suit."),
)


def classify(error: object, stage: str = "") -> LLMFailure:
    """The reason for a failed model call. Unknown errors are high: something is wrong that nobody has named yet."""
    text = " ".join(str(error).split())
    for reason, pattern, severity, summary, fix in _RULES:
        if pattern.search(text):
            return LLMFailure(reason, severity, summary, fix)
    return LLMFailure("unknown", "high", "A model call failed for a reason that is not recognised.", "Read the error text in the alert and the agent's log.")


def alert_message(f: LLMFailure, stage: str, error: object) -> str:
    """One line for the alert: the reason, the step that failed, the provider's own words (shortened), what to do."""
    raw = " ".join(str(error).split())[:240]
    return f"AI {stage or 'model'} step failed ({f.reason}): {f.summary} Provider said: {raw} Fix: {f.fix}"
