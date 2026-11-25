"""The deploy files agree with the application's own configuration rules (ADR 0025).

There is no Docker daemon in the test environment, so each part's compose file is read as data and each service is checked the
way it would start: its role and environment must satisfy the configuration validator in production mode, it must hold exactly
the secrets its component needs and none more (INV-11), every variable it passes must be a real setting (a typo would be
silently ignored), and the part's .env.example must document every variable the file references so that copying it is enough.
This keeps the deploy files honest as the code changes."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

from salesai.config import Settings

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
PARTS = ("db", "web", "gateway", "agent", "admin")

SECRETS = {"MASTER_KEY", "JWT_SECRET", "OTP_SECRET", "META_APP_SECRET", "META_VERIFY_TOKEN", "PLATFORM_WA_ACCESS_TOKEN",
           "OPENAI_COMPAT_API_KEY", "ANTHROPIC_API_KEY", "SMTP_PASSWORD", "ADMIN_JWT_SECRET", "FOUNDER_TOTP_SECRET", "SES_SECRET_ACCESS_KEY"}

# What each service may hold. This table is the architecture, written as a test: a secret added to a service must be added here
# deliberately, with a reason.
EXPECTED_SECRETS: dict[str, set[str]] = {
    "db/migrate": set(),
    "web/api": {"JWT_SECRET", "OTP_SECRET"},                                   # no master key and no admin secret: the public app cannot reach the console (ADR 0028)
    "admin/api": {"MASTER_KEY", "JWT_SECRET", "OTP_SECRET", "ADMIN_JWT_SECRET", "FOUNDER_TOTP_SECRET", "SMTP_PASSWORD", "SES_SECRET_ACCESS_KEY"},   # the console: master key for a pilot number by hand, its own secret, the founder's authenticator secret, and what it needs to email a sign-in code (ADR 0056)
    "admin/site": set(),
    "web/scheduler": {"PLATFORM_WA_ACCESS_TOKEN", "SMTP_PASSWORD", "SES_SECRET_ACCESS_KEY"},            # platform alerts: email to the founder, and Critical on WhatsApp (ADR 0029)
    "gateway/receive": {"META_APP_SECRET", "META_VERIFY_TOKEN"},
    "gateway/inbound": set(),
    "gateway/send": {"MASTER_KEY", "META_APP_SECRET", "PLATFORM_WA_ACCESS_TOKEN"},
    "gateway/scheduler": set(),
    "agent/agent": {"OPENAI_COMPAT_API_KEY", "ANTHROPIC_API_KEY"},             # the model key; never a WhatsApp token or the master key
    "agent/scheduler": set(),
}

DUMMY = {
    "FOUNDER_PHONE": "+919876543210", "FOUNDER_EMAIL": "founder@example.com", "FOUNDER_TOTP_SECRET": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
    "ADMIN_EMAIL_FROM": "ChatDeal Access <access@example.com>", "SES_REGION": "ap-south-1", "ENV": "production", "MASTER_KEY": "a" * 43 + "=", "JWT_SECRET": "j" * 40, "OTP_SECRET": "o" * 40, "META_APP_SECRET": "m" * 24,
    "META_VERIFY_TOKEN": "v" * 24, "PLATFORM_WA_ACCESS_TOKEN": "EAAplatformtoken", "OPENAI_COMPAT_API_KEY": "gsk_example",
    "ANTHROPIC_API_KEY": "sk-ant-example", "OPENAI_COMPAT_BASE_URL": "https://llm.example/v1", "LLM_PROVIDER": "openai_compat",
    "ALLOWED_ORIGINS": "https://app.example.com", "ADMIN_JWT_SECRET": "z" * 40, "ADMIN_ALLOWED_ORIGINS": "https://admin.example.com", "META_GRAPH_VERSION": "v26.0", "OTP_CHANNEL": "whatsapp_cloud",
}
DB_URL = "postgresql://u:p@db.example:5432/salesai"


def _services() -> list[tuple[str, dict[str, Any], str]]:
    out = []
    for part in PARTS:
        doc = yaml.safe_load((DEPLOY / part / "docker-compose.yml").read_text())
        for name, svc in doc["services"].items():
            out.append((f"{part}/{name}", svc, part))
    return out


def _resolve(key: str, template: Any) -> str:
    """The value a service would get from compose plus its .env: a literal as written; a template's own default; an optional
    setting with an empty default as empty; and for a required one (`${X:?...}`) or a secret the .env supplies, a stand-in."""
    s = str(template)
    m = re.fullmatch(r"\$\{([A-Z0-9_]+)(?::-(.*)|:\?.*)?\}", s)
    if not m:
        return s                                                                   # a literal
    default = m.group(2)
    if default:
        return default
    if default == "":                                                              # optional: empty unless it is a value the .env supplies
        return DUMMY.get(key, "") if key in SECRETS | {"OPENAI_COMPAT_BASE_URL", "SES_REGION"} else ""
    return DB_URL if key.endswith("_DATABASE_URL") else DUMMY.get(key, "x")       # required: the .env supplies it


def _settings_for(svc: dict[str, Any]) -> Settings:
    role = svc["command"][0]
    env = {k: _resolve(k, v) for k, v in (svc.get("environment") or {}).items()}
    env["ROLE"] = role
    # A container starts with only what its compose file gives it. The rest of the suite sets settings in this process's
    # environment (conftest), which would otherwise fill in what the file left out and hide a missing or placeholder value.
    with mock.patch.dict(os.environ, {}, clear=True):
        return Settings(_env_file=None, **{k.lower(): v for k, v in env.items()})  # type: ignore[arg-type]


RUNNABLE = [(sid, svc, part) for sid, svc, part in _services() if "command" in svc]


@pytest.mark.parametrize("sid,svc,part", RUNNABLE, ids=[r[0] for r in RUNNABLE])
def test_every_service_starts_in_production_with_what_it_is_given(sid, svc, part):
    _settings_for(svc)                                                             # raises if the validator would stop it


@pytest.mark.parametrize("sid,svc,part", RUNNABLE, ids=[r[0] for r in RUNNABLE])
def test_every_service_holds_exactly_the_secrets_its_component_needs(sid, svc, part):
    held = {k for k, v in (svc.get("environment") or {}).items() if k in SECRETS}
    assert held == EXPECTED_SECRETS[sid], f"{sid} holds {sorted(held)}, expected {sorted(EXPECTED_SECRETS[sid])}"


@pytest.mark.parametrize("sid,svc,part", RUNNABLE, ids=[r[0] for r in RUNNABLE])
def test_only_the_agents_turn_worker_gets_the_login_that_can_read_floor_prices(sid, svc, part):
    """INV-1: price_floors is readable by one database role. A breach of any other part must not be able to read a floor, so the
    role's login is given to the one service that decides turns and to none other."""
    has_it = "PRICING_DATABASE_URL" in (svc.get("environment") or {})
    assert has_it == (sid == "agent/agent"), f"{sid}: pricing login {'present' if has_it else 'absent'}"
    assert _settings_for(svc).reads_floors == has_it                              # and the application agrees about who needs it


@pytest.mark.parametrize("sid,svc,part", RUNNABLE, ids=[r[0] for r in RUNNABLE])
def test_every_variable_a_service_passes_is_a_real_setting(sid, svc, part):
    known = {n.upper() for n in Settings.model_fields} | {"WORKER_CONCURRENCY"}
    unknown = {k for k in (svc.get("environment") or {}) if k not in known}
    assert not unknown, f"{sid} passes settings the application does not know (typo?): {sorted(unknown)}"


@pytest.mark.parametrize("part", PARTS)
def test_the_env_example_documents_every_variable_the_compose_file_uses(part):
    compose = (DEPLOY / part / "docker-compose.yml").read_text()
    used = set(re.findall(r"\$\{([A-Z0-9_]+)", compose))
    documented = set(re.findall(r"^#? ?([A-Z0-9_]+)=", (DEPLOY / part / ".env.example").read_text(), re.M))   # `# KEY=` documents an optional one
    assert used <= documented, f"{part}: not in .env.example: {sorted(used - documented)}"


@pytest.mark.parametrize("part", PARTS)
def test_every_part_builds_the_same_backend_image(part):
    doc = yaml.safe_load((DEPLOY / part / "docker-compose.yml").read_text())
    images = {svc.get("image") for n, svc in doc["services"].items() if n != "site"}
    assert images == {"${BACKEND_IMAGE:-chatdeal-backend:local}"}                  # one artifact, run in different modes


def test_no_two_parts_publish_the_same_host_port():
    seen: dict[str, str] = {}
    for sid, svc, _ in _services():
        for port in svc.get("ports", []):
            host = re.sub(r"\$\{[A-Z0-9_]+:-(\d+)\}", r"\1", str(port)).split(":")[-2]
            assert host not in seen, f"{sid} and {seen[host]} both publish {host}"
            seen[host] = sid


@pytest.mark.parametrize("part", ["web", "gateway", "agent", "admin"])
def test_every_setting_a_part_documents_actually_reaches_its_containers(part):
    """The reverse of the test above. A compose file hands each container only the settings it lists, so a setting that is documented in .env.example
    but missing from the file is silently ignored (the operator edits it and nothing changes). Optional lines (`# KEY=`) are exempt."""
    text = (DEPLOY / part / "docker-compose.yml").read_text()
    documented = set(re.findall(r"^([A-Z0-9_]+)=", (DEPLOY / part / ".env.example").read_text(), re.M))
    unused = sorted(k for k in documented if "${" + k not in text)
    assert not unused, f"{part}: documented in .env.example but never passed to a container: {unused}"


def test_the_only_services_that_publish_a_port_are_the_ones_meant_to():
    published = {sid for sid, svc, _ in _services() if svc.get("ports")}
    assert published == {"web/site", "gateway/receive", "admin/site"}             # the website, Meta's webhook and the founder's console, nothing else


def test_published_ports_are_this_machine_only_by_default_and_widened_only_by_setting():
    """The difference between a laptop and a real server is configuration only: every port is private unless WEB_BIND / ADMIN_BIND say otherwise, so a
    forgotten setting can never expose the site or the console. The webhook receiver and the database are never published beyond this machine."""
    for sid, svc, _ in _services():
        for port in svc.get("ports", []):
            text = str(port)
            if sid in ("web/site", "admin/site"):
                assert text.startswith(("${WEB_BIND:-127.0.0.1}:", "${ADMIN_BIND:-127.0.0.1}:")), f"{sid} is not private by default, or cannot be widened by setting: {text}"
            else:
                assert text.startswith("127.0.0.1:"), f"{sid} publishes {text} beyond this machine"


def test_the_old_combined_deploy_files_are_gone():
    assert not (DEPLOY / "standalone").exists()


# ---------------------------------------------------------------- the founder's console is served strictly (ADR 0028)

def test_the_consoles_web_server_sets_the_strictest_headers_and_proxies_only_the_consoles_routes():
    caddy = (DEPLOY.parent / "frontend" / "deploy" / "Caddyfile.admin").read_text()
    csp = next(line for line in caddy.splitlines() if "Content-Security-Policy" in line)
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "base-uri 'none'" in csp
    assert "https://" not in csp and "unsafe-eval" not in csp and "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("style-src-attr")[0]
    for header in ("X-Frame-Options DENY", "X-Content-Type-Options nosniff", "Referrer-Policy no-referrer", 'Cache-Control "no-store"', "Strict-Transport-Security"):
        assert header in caddy
    proxied = re.findall(r"handle (/\S+)", caddy)
    assert set(proxied) >= {"/admin/v1/*", "/api/v1/operator/*"}
    assert not any(p in proxied for p in ("/api/*", "/webhooks/*", "/api/v1/*")), proxied               # the shop API and Meta's webhook are not reachable through the console
    compose = yaml.safe_load((DEPLOY / "admin" / "docker-compose.yml").read_text())
    assert "INGRESS_UPSTREAM" not in str(compose["services"]["site"]["environment"])


def test_the_consoles_image_is_built_from_the_consoles_own_bundle():
    docker = (DEPLOY.parent / "frontend" / "Dockerfile.admin").read_text()
    assert "build:admin" in docker and "dist-admin" in docker and "Caddyfile.admin" in docker


def test_the_one_machine_stack_also_gives_the_floor_login_only_to_the_services_that_decide_turns():
    doc = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
    holders = {n for n, svc in doc["services"].items() if "PRICING_DATABASE_URL" in (svc.get("environment") or {})}
    assert holders == {"worker", "seed"}, holders


@pytest.mark.parametrize("sid,svc,part", RUNNABLE, ids=[r[0] for r in RUNNABLE])
def test_only_the_console_is_given_the_login_that_reads_admin_sign_in_secrets(sid, svc, part):
    """ADR 0043: admin credentials and sessions are readable by app_console alone, so a breach of the web, gateway or agent part cannot read them."""
    assert ("CONSOLE_DATABASE_URL" in (svc.get("environment") or {})) == (sid == "admin/api"), sid


def test_the_stand_in_wiring_script_only_writes_settings_each_part_documents_and_passes_on():
    """scripts/connect-meta-sim.ps1 edits the Meta lines of two parts' .env files. A key it writes that the part neither documents nor hands to a
    container would be silently ignored, which is how a stand-in that 'is connected' ends up talking to nothing."""
    script = (DEPLOY.parent / "scripts" / "connect-meta-sim.ps1").read_text()
    for var, part in (("gw", "gateway"), ("web", "web")):
        keys = set(re.findall(rf"Set-EnvValue \${var} '([A-Z0-9_]+)'", script))
        assert keys, f"no keys found for {part}"
        documented = set(re.findall(r"^#? ?([A-Z0-9_]+)=", (DEPLOY / part / ".env.example").read_text(), re.M))
        compose = (DEPLOY / part / "docker-compose.yml").read_text()
        assert keys <= documented, f"{part}: the script writes keys the .env.example does not document: {sorted(keys - documented)}"
        assert all("${" + k in compose for k in keys), f"{part}: written but never passed to a container: {sorted(k for k in keys if '${' + k not in compose)}"


@pytest.mark.parametrize("template", sorted([*DEPLOY.glob("*/.env.example"), DEPLOY.parent / ".env.example", DEPLOY / "all.env.example"]), ids=lambda p: str(p.relative_to(DEPLOY.parent)))
def test_no_template_line_has_an_empty_value_followed_by_a_comment(template):
    """`KEY=   # note` is read by docker compose as the value "# note", so an untouched optional setting becomes garbage (an address list, an API key).
    A note goes on the line above; an empty setting is just `KEY=`."""
    bad = [ln for ln in template.read_text().splitlines() if re.match(r"^[A-Z0-9_]+=\s+#", ln)]
    assert not bad, f"{template.name}: put these notes on their own line: {[b.split('=')[0] for b in bad]}"


# ---------------------------------------------------------------- the one shared file for an easy deploy (deploy/all.env)

def _all_compose_vars() -> set[str]:
    return {v for part in PARTS for v in re.findall(r"\$\{([A-Z0-9_]+)", (DEPLOY / part / "docker-compose.yml").read_text())}


def test_the_shared_env_example_only_names_settings_some_part_actually_uses():
    """A line in deploy/all.env that no compose file passes on would be silently ignored: the operator edits it and nothing changes."""
    text = (DEPLOY / "all.env.example").read_text()
    documented = set(re.findall(r"^#? ?([A-Z][A-Z0-9_]*)=", text, re.M))
    assert documented, "deploy/all.env.example documents nothing"
    unused = sorted(documented - _all_compose_vars())
    assert not unused, f"in deploy/all.env.example but passed by no compose file: {unused}"


def test_the_shared_env_example_covers_every_mandatory_setting_of_every_part():
    """Fill in this one file and each part starts: every setting a compose file requires (`${X:?...}`) is in it."""
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", (DEPLOY / "all.env.example").read_text(), re.M))
    required = set()
    for part in PARTS:
        required |= set(re.findall(r"\$\{([A-Z0-9_]+):\?", (DEPLOY / part / "docker-compose.yml").read_text()))
    assert not (required - documented), f"required by a compose file but missing from deploy/all.env.example: {sorted(required - documented)}"


def test_the_shared_env_file_is_never_committed_and_the_scripts_read_it_before_the_parts_own_file():
    ignore = (DEPLOY.parent / ".gitignore").read_text()
    assert "deploy/all.env" in ignore.replace("\\", "/")
    ps, sh = (DEPLOY.parent / "scripts" / "stack.ps1").read_text(), (DEPLOY.parent / "scripts" / "stack.sh").read_text()
    assert ps.index("$shared") < ps.index("$envFile") and "all.env" in ps                 # the shared file comes first, so the part's own file wins
    assert sh.index('here/all.env') < sh.index('"$dir/.env"') and "all.env" in sh
    assert "Merge-Env" in ps and "merge_env" in sh                                        # an empty value in a part's own file must not blank one set in the shared file


def test_a_shared_value_survives_an_empty_line_in_a_parts_own_file():
    """The rule the scripts implement, stated on the sample both files really have: copying a part's .env.example gives empty lines for settings that
    all.env.example fills in once; the merge keeps the filled one."""
    def merge(*texts: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for text in texts:
            for ln in text.splitlines():
                m = re.match(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$", ln)
                if m:
                    v = m.group(2).strip()
                    v = "" if v.startswith("#") else v
                    if v or m.group(1) not in out:
                        out[m.group(1)] = m.group(2)
        return out
    merged = merge("JWT_SECRET=from-shared\nSES_REGION=ap-south-1\n", "JWT_SECRET=\nSES_REGION=   # note\nAPP_ENV=dev\n")
    assert merged["JWT_SECRET"] == "from-shared" and merged["SES_REGION"] == "ap-south-1" and merged["APP_ENV"] == "dev"
    assert merge("JWT_SECRET=shared\n", "JWT_SECRET=part\n")["JWT_SECRET"] == "part"        # a non-empty value in the part's own file still wins
