# Instructions for coding agents

This file is the entry point for any coding agent (Claude Code, Codex, Cursor, …). The repository is self-contained:
everything needed to continue the work is committed.

1. Read `README.md`, then `docs/PROGRESS.md` (status, open issues, **next steps**), then `CLAUDE.md` (operating rules).
2. Treat `docs/PRD.md` as decided product behaviour and `docs/TECHNICAL_DESIGN.md` invariants `INV-1…12` as fixed.
   Anything that changes product behaviour, needs a paid commitment, or would weaken an invariant goes to the founder:
   write it down in `docs/PROGRESS.md` under "Decisions needed from the founder" and continue with something else.
3. Work in small commits that reference requirement and invariant IDs. Record each significant decision as an ADR in
   `docs/decisions/` (supersede, never rewrite).
4. Before every commit run the gates (all must pass; commands in `docs/RUNNING.md`):
   `ruff check`, `mypy src`, `python scripts/check_boundaries.py`, `pytest`, and in `frontend/`:
   `npm run typecheck`, `npm test`, `npm run build`. If you change an API route or model, regenerate the contract
   (`python scripts/export_openapi.py`, then `npm run gen:api`). Backend work on the async pipeline must also pass
   `TEST_QUEUE_BACKEND=redis` (see `docs/RUNNING.md`).
5. Never mock product behaviour in tests: tests use a real Postgres, the real queue, the real pipeline and the simulated
   WhatsApp network. Only external vendors are replaced, by local fakes that speak their wire format
   (`backend/tests/fake_anthropic.py`, `fake_graph.py`).
6. Update `docs/PROGRESS.md` at the end of every session: what is done, results, open issues, next steps.
7. Do not deploy anything and do not create pull requests unless the founder asks.
