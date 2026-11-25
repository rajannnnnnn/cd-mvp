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
# Each part reads its own deploy/<part>/.env (copy .env.example) and, first, the optional shared deploy/all.env (copy all.env.example): one file for an easy deploy,
# the part's own file wins. Either way a part is handed only the settings its own compose file names, so it gets only its own secrets.
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
tmp_files=()
trap 'rm -f "${tmp_files[@]:-}"' EXIT          # a merged copy holds every secret: it never outlives the command

# Layered settings: later files win, but an EMPTY value never blanks one set earlier (a part's .env copied from its example is full of empty lines).
merge_env() {
  awk '/^[[:space:]]*[A-Z][A-Z0-9_]*=/ {
         line=$0; sub(/^[[:space:]]+/, "", line); k=substr(line, 1, index(line, "=") - 1); v=substr(line, index(line, "=") + 1)
         t=v; sub(/^[[:space:]]+/, "", t); if (t ~ /^#/) t=""
         if (t != "" || !(k in seen)) { if (!(k in seen)) order[++n]=k; val[k]=v; seen[k]=1 }
       }
       END { for (i = 1; i <= n; i++) print order[i] "=" val[order[i]] }' "$@"
}

run_part() {
  local p="$1" dir="$here/$1"
  [ -d "$dir" ] || { echo "unknown part '$p' (db | web | gateway | agent | admin | all)" >&2; exit 1; }
  local files=()
  [ -f "$here/all.env" ] && files+=("$here/all.env")
  [ -f "$dir/.env" ] && files+=("$dir/.env")
  [ ${#files[@]} -gt 0 ] || { echo "Missing $dir/.env. Copy $dir/.env.example to $dir/.env and fill it in, or fill in the one shared file: copy $here/all.env.example to $here/all.env." >&2; exit 1; }
  local envfile="${files[0]}"
  if [ ${#files[@]} -gt 1 ]; then envfile="$(mktemp)"; tmp_files+=("$envfile"); merge_env "${files[@]}" > "$envfile"; fi
  local base=(docker compose --env-file "$envfile" -f "$dir/docker-compose.yml")
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
