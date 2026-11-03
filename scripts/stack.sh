#!/usr/bin/env bash
# Run one part of the product as its own docker compose project (ADR 0025). Same four parts as scripts/stack.ps1.
#
#   scripts/stack.sh db                          run the database migrations (a one-off job; do this first and after every release)
#   scripts/stack.sh web                         the site, its API and its timer
#   scripts/stack.sh gateway                     the only part that talks to Meta
#   scripts/stack.sh agent                       the ChatDeal AI
#   scripts/stack.sh admin                       the founder's console (its own address and sign-in)
#   scripts/stack.sh agent --scale agent=3       run 3 copies of one service (agent | send | inbound | api ...)
#   scripts/stack.sh agent logs | ps | down      look at, or stop, a part
#   scripts/stack.sh all                         db, then web, admin, gateway and agent: everything on one machine
#   add --build to rebuild the image first
#
# Each part reads only its own deploy/<part>/.env (copy .env.example), so it is handed only its own secrets.
# The parts share the database and nothing else, so you can run any subset, on any machines.
set -euo pipefail

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
[ $# -ge 1 ] || usage 1
part="$1"; shift
action=up; build=0; scales=()
while [ $# -gt 0 ]; do
  case "$1" in
    up|down|logs|ps) action="$1" ;;
    --build) build=1 ;;
    --scale) shift; [ $# -gt 0 ] || usage 1; scales+=(--scale "$1") ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
  shift
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy" && pwd)"

run_part() {
  local p="$1" dir="$here/$1"
  [ -d "$dir" ] || { echo "unknown part '$p' (db | web | gateway | agent | admin | all)" >&2; exit 1; }
  [ -f "$dir/.env" ] || { echo "Missing $dir/.env. Copy $dir/.env.example to $dir/.env and fill it in." >&2; exit 1; }
  local base=(docker compose --env-file "$dir/.env" -f "$dir/docker-compose.yml")
  case "$action" in
    up)
      if [ "$p" = db ]; then
        [ "$build" = 1 ] && "${base[@]}" build
        "${base[@]}" run --rm migrate
      else
        local a=(up -d); [ "$build" = 1 ] && a+=(--build)
        "${base[@]}" "${a[@]}" "${scales[@]}"
      fi ;;
    down) [ "$p" = db ] || "${base[@]}" down ;;
    logs) [ "$p" = db ] || "${base[@]}" logs -f --tail 50 ;;
    ps)   [ "$p" = db ] || "${base[@]}" ps ;;
  esac
}

if [ "$part" = all ]; then
  if [ "$action" = down ]; then order=(agent gateway admin web); else order=(db web admin gateway agent); fi
  for p in "${order[@]}"; do echo "== $p =="; run_part "$p"; done
else
  run_part "$part"
fi
