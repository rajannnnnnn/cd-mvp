# 0053 A record of every attempt to tell the founder, shown in the console

**Status:** accepted · 2026-11-24 · founder: "can we load it in the console, like what are the mails sent, which reads from the db"

## Context
`operator_alerts.notified_at` says an alert was handed to a sink, not when, how often, or with what result. A successful send left no log line, and SES has no "sent" folder. The founder could not answer "did the mail go out?" without opening their own inbox.

## Decision
A platform table `alert_deliveries` (migration 0037): alert id (null for a digest), kind (alert, digest, recovery), severity, sink, accepted or not, the provider's own reference (the SES message id), an error text, how many alerts the message carried, and the time. The router writes one row and one log line per attempt, and a failed write never stops an alert. The founder console's Alerts screen has a third view, "Sent to you", read from `GET /api/v1/operator/alerts/deliveries`.
It stores no recipient address and no alert text. The alert path's structural guard test now allows this one extra table and still forbids owner numbers and shop users.

## Consequences
"Accepted" means the provider took the message. Arrival, bounces and complaints are only visible in SES (suppression list, or a configuration set with events), not here. The table grows by one row per attempt; add it to retention if it ever matters.

## How to revisit
Drop the table and the router's `_record`. To show bounces here, add SES event notifications and write them into the same table.
