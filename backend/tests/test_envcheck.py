"""A part that cannot start says what is missing in plain words and exits; it never prints a stack trace. The role is the command's role
(a migration job needs only the database URLs), and optional settings are advice, never a block."""
from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from salesai.config import Settings
from salesai.envcheck import advisories, explain

DB = {"DATABASE_URL": "postgresql://x", "SYSTEM_DATABASE_URL": "postgresql://x", "MIGRATION_DATABASE_URL": "postgresql://x"}


def run(args: list[str], env: dict[str, str], cwd) -> subprocess.CompletedProcess[str]:
    """`python -m salesai ...` in a clean environment and an empty folder (so no .env file is read)."""
    return subprocess.run([sys.executable, "-m", "salesai", *args], env={"PATH": "/usr/bin:/bin", **env}, cwd=cwd, capture_output=True, text=True, timeout=60)  # noqa: S603


def test_a_missing_setting_is_named_and_marked_required(monkeypatch):
    for name in ("CONSOLE_DATABASE_URL", "ADMIN_JWT_SECRET", "JWT_SECRET", "OTP_SECRET", "MASTER_KEY", "ROLE"):
        monkeypatch.delenv(name, raising=False)              # other tests export the whole secret set into this process
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, role="admin", database_url="x", system_database_url="x", migration_database_url="x")      # type: ignore[arg-type]
    text = explain(e.value, "admin")
    assert 'Cannot start the "admin" part' in text and "Required" in text and "CONSOLE_DATABASE_URL" in text and "Traceback" not in text
    assert ".env" in text and "doctor" in text


def test_a_part_with_nothing_set_prints_a_report_and_exits_one_without_a_stack_trace(tmp_path):
    r = run(["worker"], {}, tmp_path)
    assert r.returncode == 1 and "Cannot start the \"worker\" part" in r.stderr and "Traceback" not in r.stderr and "DATABASE_URL" in r.stderr


def test_an_unknown_role_is_reported_plainly(tmp_path):
    r = run(["frobnicate"], DB, tmp_path)
    assert r.returncode == 1 and "ROLE" in r.stderr and "Traceback" not in r.stderr


def test_the_migration_job_needs_only_the_database_urls(tmp_path, pg_urls):
    r = run(["migrate"], {"DATABASE_URL": pg_urls["user"], "SYSTEM_DATABASE_URL": pg_urls["system"], "MIGRATION_DATABASE_URL": pg_urls["owner"]}, tmp_path)
    assert r.returncode == 0, r.stderr                                     # the role is `migrate`, not the everything-in-one default that wants every secret


def test_an_unreachable_database_is_explained_not_dumped(tmp_path):
    bad = "postgresql://nobody:x@127.0.0.1:1/none"
    r = run(["migrate"], {"DATABASE_URL": bad, "SYSTEM_DATABASE_URL": bad, "MIGRATION_DATABASE_URL": bad}, tmp_path)
    assert r.returncode == 1 and "not reachable" in r.stderr and "host.docker.internal" in r.stderr and "Traceback" not in r.stderr


def test_the_doctor_for_a_part_reports_what_is_missing_for_that_part(tmp_path):
    r = run(["doctor", "admin"], DB, tmp_path)
    assert r.returncode == 1 and "CONSOLE_DATABASE_URL" in r.stderr and "ADMIN_JWT_SECRET" in r.stderr and "Traceback" not in r.stderr


def test_optional_settings_are_advice_and_never_block(monkeypatch):
    for name in ("ALERT_EMAIL_TO", "SMTP_HOST", "INVOICE_SELLER_NAME", "ROLE"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None, role="api", jwt_secret="j" * 40, otp_secret="o" * 40, database_url="x", system_database_url="x",
                 migration_database_url="x", simulator_enabled=False)      # type: ignore[arg-type]
    notes = " ".join(advisories(s))
    assert "ALERT_EMAIL_TO" in notes and "INVOICE_SELLER" in notes
