#!/usr/bin/env bash
# Local development database + .env. Usage: scripts/dev_env.sh  (needs a local Postgres superuser; see docs/RUNNING.md)
set -euo pipefail
cd "$(dirname "$0")/.."
SUPER="${DEV_PG_SUPERUSER_URL:-postgresql://postgres@127.0.0.1:5432/postgres}"
DB="${DEV_DB:-salesai_dev}"
psql "$SUPER" -tc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q 1 || psql "$SUPER" -c "CREATE DATABASE $DB"
DBURL="${SUPER%/*}/$DB"
psql "$DBURL" -v ON_ERROR_STOP=1 -q -v owner_pw=owner_pw -v user_pw=user_pw -v pricing_pw=pricing_pw -v system_pw=system_pw -f db/bootstrap_roles.sql
HOSTPART="127.0.0.1:5432"
cat > .env <<ENVEOF
ENV=dev
ROLE=all
HTTP_PORT=8000
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:8080,http://127.0.0.1:5173
DATABASE_URL=postgresql://app_user:user_pw@$HOSTPART/$DB
PRICING_DATABASE_URL=postgresql://app_pricing:pricing_pw@$HOSTPART/$DB
SYSTEM_DATABASE_URL=postgresql://app_system:system_pw@$HOSTPART/$DB
MIGRATION_DATABASE_URL=postgresql://app_owner:owner_pw@$HOSTPART/$DB
MASTER_KEY=$(head -c 32 /dev/urandom | base64)
JWT_SECRET=dev-jwt-$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
OTP_SECRET=dev-otp-$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
META_APP_SECRET=dev-meta-secret
META_VERIFY_TOKEN=dev-verify-token
OTP_MIN_INTERVAL_S=1
OTP_PER_PHONE_PER_HOUR=500
OTP_PER_IP_PER_HOUR=2000
SIMULATOR_ENABLED=true
LLM_PROVIDER=local
ENVEOF
echo ".env written for database $DB"
