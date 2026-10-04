# 0017 Alert routing to the platform team

**Status:** accepted · 2026-10-04

## Context
Operator alerts (disconnected numbers, dead letters, failed sends, quality drops) existed only as rows shown in the operator
console, so nobody was told. A small team needs them pushed to where it already looks.

## Options
Email (needs a mail vendor), a chat webhook, WhatsApp to the team's own numbers, a hosted incident tool.

## Decision
A small `alerting` module with two sinks, both optional and configured by environment: a JSON webhook (`ALERT_WEBHOOK_URL`;
the body has a `text` field that Slack/Teams/Discord-style incoming webhooks accept) and WhatsApp to
`ALERT_WHATSAPP_NUMBERS` from the platform sender using the `operator_alert` template. The leader-elected scheduler
dispatches every ~30 s: an alert is marked notified once any sink accepts it (`operator_alerts.notified_at`), unresolved
critical alerts repeat every `ALERT_REMINDER_MINUTES`, a failing sink is retried up to five times without blocking others,
and `ALERT_MIN_SEVERITY` filters. Alert text carries severity, business name and the one-line message, never customer content.

## Consequences
With nothing configured behaviour is unchanged (console only). The WhatsApp sink needs the `operator_alert` template
approved in Meta before it works outside the simulator. Email can be added as a third sink without touching callers.

## How to revisit
Add a sink implementing `Sink.send`; tests are in `tests/test_alert_routing.py` and run against a real local HTTP endpoint.
