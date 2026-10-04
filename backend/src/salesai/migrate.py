"""Versioned SQL migrations, run as a separate one-off step (never at process start).

Files: migrations/NNNN_name.sql applied in order, each in its own transaction, recorded in
schema_migrations with a checksum. migrations/_grants.sql is re-applied after every run.
Policy: each migration must be backward compatible with the previous release (add, don't
rename/drop in the same release) so old and new workers can run side by side."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import psycopg

log = logging.getLogger("salesai.migrate")

LOCK_ID = 7_283_461_001


def migrations_dir() -> Path:
    env = os.environ.get("MIGRATIONS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "migrations"


def run_migrations(url: str, directory: Path | None = None) -> list[str]:
    d = directory or migrations_dir()
    files = sorted(p for p in d.glob("*.sql") if not p.name.startswith("_"))
    applied_now: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
            for f in files:
                sql = f.read_text()
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                if f.stem in done:
                    if done[f.stem] != checksum:
                        raise RuntimeError(f"migration {f.name} was modified after being applied")
                    continue
                log.info("applying %s", f.name)
                with conn.transaction():
                    conn.execute(sql)  # type: ignore[arg-type]
                    conn.execute("INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", (f.stem, checksum))
                applied_now.append(f.stem)
            grants = d / "_grants.sql"
            if grants.exists():
                with conn.transaction():
                    conn.execute(grants.read_text())  # type: ignore[arg-type]
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied_now
