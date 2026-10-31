"""python -m metasim --port 9100 [--webhook-url http://127.0.0.1:8001/webhooks/whatsapp] [--app-secret ...] [--verify-token ...]"""
from __future__ import annotations

import argparse

import uvicorn

from metasim.app import app, sim


def main() -> None:
    ap = argparse.ArgumentParser(description="Independent Meta simulator and WhatsApp test frontend")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--webhook-url")
    ap.add_argument("--app-secret")
    ap.add_argument("--verify-token")
    a = ap.parse_args()
    if a.webhook_url:
        sim.settings.webhook_url = a.webhook_url
    if a.app_secret:
        sim.settings.app_secret = a.app_secret
    if a.verify_token:
        sim.settings.verify_token = a.verify_token
    print(f"meta-sim UI  http://{a.host}:{a.port}/   Graph base (META_GRAPH_BASE) http://{a.host}:{a.port}   webhook -> {sim.settings.webhook_url}", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
