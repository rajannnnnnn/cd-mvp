# CONTINUATION NOTES

Short handoff so work can resume on any machine. Read this, then `docs/PROGRESS.md` (the long-term memory).
Update this file at the end of every session and push (see "Backup rule").

_Last updated: 2026-10-31._

## Backup rule (founder's instruction)
Code must never live on one machine only. Work is committed and pushed to the remote branch
`backup/propelry-wip` (separate from `propelry` and `claude/festive-allen-4dinya`) at the end of every
work chunk. Never commit `.env` files, `DockerInstaller.exe`, or other binaries. No pull requests unless asked.

## Where things stand
- Branch history: base `propelry`; the backup branch carries the same work plus uncommitted changes
  that existed on 2026-10-31.
- Work in flight: **channel gateway** (ADR 0020), with `deploy/gateway/` (receive / inbound / send parts),
  `deploy/standalone/`, migration `0012_webhook_raw_log.sql`, `backend/tests/test_gateway_raw_log.py`,
  `docs/WHATSAPP_GO_LIVE.md`, and helper scripts `scripts/stack.ps1`, `scripts/demo.ps1`, `scripts/dev-frontend.ps1`.
- The full backend suite was **not run** in the last session: this machine had no Docker daemon, Postgres or `uv`.

## Update 2026-10-31 (evening)
- ADR 0020 follow-ups 1-4 are built and committed (`cfd8d42`, `154e822`). Their 28 tests pass; ruff, mypy and `check_boundaries.py` are clean.
- **The full backend suite has NOT completed.** The run was stopped by the system for low memory. Re-run it when memory is free:
  `cd backend; $env:TEST_PG_SUPERUSER_URL='postgresql://postgres@127.0.0.1:55432/postgres'; python -m pytest -q`
  (Postgres is the `cd-test-pg` Docker container; this PC has no `psql`, so put a `psql` shim that runs `docker exec cd-test-pg psql` on PATH.)
  Python venv: `C:\Users\user\.venvs\salesai` (created with pip, not uv). Use `PYTHONUTF8=1` for `scripts/check_boundaries.py`.
- Not pushed: GitHub sign-in is needed once (`git push -u origin backup/propelry-wip`).
- Next component: the agent (M7). Needs an Anthropic API key to run the evaluations on a real model.
- `deploy/gateway/.env.example`: a blank value followed by `# comment` becomes the comment text in compose; move comments to their own lines.

## Next steps (from ADR 0020 and PROGRESS.md) — items 1-4 below are now done
1. Raw webhook log fidelity: exact bytes, signature header, keep bad-signature requests, `RAW_WEBHOOK_RETENTION_DAYS`
   (tests first; `test_gateway_raw_log.py` exists).
2. Per-role secrets: only `send` needs `MASTER_KEY`.
3. Agent context loader must not read `access_token_enc`.
4. `/metrics` ports for the monitor.
5. Run the full backend suite on a machine with Postgres (Docker Desktop installer was downloaded; verify it is installed).

## Open founder question
Should the owner's WhatsApp `stop` / `start` commands (PRD FR-CV-10) be removed along with the app toggle?

## Security check, 2026-10-31
No sign of compromise: Docker installer validly signed, all commits are from this project, no secrets tracked.
Port 22 (SSH) was listening on all interfaces of the dev PC; disable `sshd` if it was not turned on deliberately.
