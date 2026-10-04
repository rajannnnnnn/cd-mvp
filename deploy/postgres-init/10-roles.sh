#!/bin/sh
# Runs once, on first start of an empty data directory. Creates the four least-privilege roles (INV-1, INV-5).
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_pw="$APP_OWNER_PASSWORD" -v user_pw="$APP_USER_PASSWORD" \
  -v pricing_pw="$APP_PRICING_PASSWORD" -v system_pw="$APP_SYSTEM_PASSWORD" \
  -f /bootstrap_roles.sql
