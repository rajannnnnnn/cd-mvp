"""Migrations that touch existing rows must work on a database that already has data (a fresh test database has none)."""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import psycopg

from salesai.migrate import migrations_dir, run_migrations
from tests.conftest import PW, ROOT, SUPER, _with_db


def test_slug_backfill_gives_every_existing_business_a_unique_valid_slug(tmp_path: Path):
    name = f"salesai_mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(SUPER, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    try:
        subprocess.run(["psql", _with_db(SUPER, name), "-v", "ON_ERROR_STOP=1", "-q", "-v", f"owner_pw={PW['owner']}", "-v", f"user_pw={PW['user']}",  # noqa: S603,S607
                        "-v", f"pricing_pw={PW['pricing']}", "-v", f"system_pw={PW['system']}", "-f", str(ROOT / "db" / "bootstrap_roles.sql")], check=True, capture_output=True)
        owner = _with_db(SUPER, name, "app_owner", PW["owner"])
        before = tmp_path / "before"
        before.mkdir()
        for f in sorted(migrations_dir().glob("000[1-4]_*.sql")):
            shutil.copy(f, before / f.name)
        grants = (migrations_dir() / "_grants.sql").read_text()
        (before / "_grants.sql").write_text(grants.split("-- ---------------------------------------------------------------- billing")[0])   # the grants of that era
        run_migrations(owner, before)                                           # the schema as it was before slugs existed
        with psycopg.connect(_with_db(SUPER, name), autocommit=True) as c:      # existing shops, some with the same or awkward names
            for n in ("Sharma Sarees & Fabrics", "Sharma Sarees & Fabrics", "Gupta Mobile!!", "###", "Pricing"):
                c.execute("INSERT INTO businesses (name) VALUES (%s)", (n,))
        run_migrations(owner)                                                   # now apply 0005 and later on top of that data
        with psycopg.connect(_with_db(SUPER, name), autocommit=True) as c:
            slugs = [r[0] for r in c.execute("SELECT slug FROM businesses ORDER BY created_at, id")]
            policy = c.execute("SELECT relforcerowsecurity FROM pg_class WHERE relname='businesses'").fetchone()
        assert len(slugs) == len(set(slugs)) == 5 and all(3 <= len(s) <= 40 for s in slugs)
        assert slugs[0] == "sharma-sarees-fabrics" and slugs[1] == "sharma-sarees-fabrics-2" and slugs[2] == "gupta-mobile" and slugs[3] == "shop"
        assert policy is not None and policy[0] is True                         # row-level security is forced again afterwards
    finally:
        with psycopg.connect(SUPER, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
