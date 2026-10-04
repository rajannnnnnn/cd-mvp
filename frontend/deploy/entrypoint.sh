#!/bin/sh
# The SPA reads /config.json at runtime, so one built image works in every environment.
# API_BASE="" means "same origin" (Caddy proxies /api to the backend); set it to an absolute URL to point elsewhere.
set -eu
printf '{"apiBase":"%s"}\n' "${API_BASE:-}" > /srv/config.json
exec "$@"
