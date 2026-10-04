"""Writes the API contract (openapi.json) that the frontend client is generated from.  python scripts/export_openapi.py"""
import base64
import json
import os
import pathlib

for k, v in {"DATABASE_URL": "postgresql://x", "PRICING_DATABASE_URL": "postgresql://x", "SYSTEM_DATABASE_URL": "postgresql://x", "MIGRATION_DATABASE_URL": "postgresql://x",
             "MASTER_KEY": base64.b64encode(b"k" * 32).decode(), "JWT_SECRET": "x" * 32, "OTP_SECRET": "x" * 32, "META_APP_SECRET": "xxxxxxxx", "META_VERIFY_TOKEN": "xxxxxxxx"}.items():
    os.environ.setdefault(k, v)

from salesai.api.app import create_app  # noqa: E402

spec = create_app(None, "web").openapi()
out = pathlib.Path(__file__).resolve().parents[1] / "openapi.json"
out.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
print(f"wrote {out} ({len(spec['paths'])} paths)")
