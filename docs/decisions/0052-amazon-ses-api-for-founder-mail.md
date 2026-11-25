# 0052 Amazon SES API as a second way to send founder alert mail

**Status:** accepted · 2026-11-24 · founder: add the SES API sender using boto3; configuration in env; nothing may break

## Context
ADR 0029 sends founder alerts over plain SMTP. Amazon SES SMTP needs its own SMTP credentials; the founder's tested path uses the SES API with an AWS access key.

## Options
1. SMTP only, tell the founder to create SMTP credentials. No new dependency, but a second set of credentials.
2. **Add the SES API as a second transport, chosen by `EMAIL_TRANSPORT`.** One new dependency (boto3).
3. Replace SMTP with SES. Rejected: it locks the vendor in.

## Decision
Option 2. `EMAIL_TRANSPORT=smtp` (default, unchanged) or `ses`. For `ses`: `SES_REGION` (required), `SES_ACCESS_KEY_ID` and `SES_SECRET_ACCESS_KEY` (both or neither; neither means boto3's own credential chain, an IAM role). Only the web part's scheduler sends mail, so only it receives these. `python -m salesai doctor web` checks the keys and region (`get_send_quota`) and `--send-test-mail` sends one message. boto3 is imported only when SES is chosen. The sink is the same class; the transport is a function passed to it.

## Consequences
SMTP deployments are untouched. The image must be rebuilt to include boto3. The SES sandbox rules still apply (verified sender, and verified recipient until production access). Client-facing mail is still not built.

## How to revisit
Set `EMAIL_TRANSPORT=smtp`. To drop SES, delete `ses_deliver` and `ses_check` and the four settings.
