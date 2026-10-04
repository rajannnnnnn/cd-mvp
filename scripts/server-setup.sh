#!/usr/bin/env bash
# One-command demo deployment for a fresh Ubuntu/Debian server (run as root or with sudo).
#
#   curl -fsSL https://raw.githubusercontent.com/rajannnnnnn/cd-mvp/claude/festive-allen-4dinya/scripts/server-setup.sh | sudo bash
#
# Optional settings (put them in front of "sudo bash" as VAR=value):
#   DOMAIN=demo.example.com   serve on that name with automatic HTTPS (its DNS A record must already point to this server)
#   ALLOW_IPS="1.2.3.4 5.6.7.8"   only these addresses may open the demo (everyone else gets 403). STRONGLY recommended.
#   BRANCH=...  REPO=...      what to deploy (defaults: this branch of the project repository)
#
# This is a DEMO deployment: simulated WhatsApp, built-in stand-in AI, test payments, and ANY login code is accepted.
set -euo pipefail
REPO="${REPO:-https://github.com/rajannnnnnn/cd-mvp.git}"
BRANCH="${BRANCH:-claude/festive-allen-4dinya}"
DIR=/opt/saathi

if [ "$(id -u)" -ne 0 ]; then echo "Run as root: sudo bash scripts/server-setup.sh"; exit 1; fi

echo "==> installing Docker and Git if missing"
command -v git >/dev/null || (apt-get update -y && apt-get install -y git curl ca-certificates)
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh

echo "==> fetching the code ($BRANCH)"
if [ -d "$DIR/.git" ]; then git -C "$DIR" fetch origin "$BRANCH" && git -C "$DIR" checkout "$BRANCH" && git -C "$DIR" pull --ff-only origin "$BRANCH"
else git clone --branch "$BRANCH" "$REPO" "$DIR"; fi
cd "$DIR"

if [ -n "${DOMAIN:-}" ]; then export SITE_ADDRESS="$DOMAIN" WEB_PORT=80 HTTPS_PORT=443; else export SITE_ADDRESS=":80" WEB_PORT=80 HTTPS_PORT=8443; fi
export ALLOW_IPS="${ALLOW_IPS:-0.0.0.0/0 ::/0}"

echo "==> building and starting (first time takes several minutes)"
scripts/up.sh --demo

IP=$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
echo
if [ -n "${DOMAIN:-}" ]; then echo "Open https://$DOMAIN"; else echo "Open http://$IP"; fi
if [ "$ALLOW_IPS" = "0.0.0.0/0 ::/0" ]; then
  echo "WARNING: anyone with the link can sign in (demo mode accepts any code). Lock it to your own addresses with:"
  echo "  ALLOW_IPS=\"your.ip.here\" bash $DIR/scripts/server-setup.sh"
fi
echo "Stop: cd $DIR && scripts/up.sh --down     Wipe: scripts/up.sh --reset"
