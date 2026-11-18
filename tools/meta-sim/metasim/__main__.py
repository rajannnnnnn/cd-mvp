"""python -m metasim --port 9100 [--webhook-url http://127.0.0.1:8001/webhooks/whatsapp] [--app-secret ...] [--verify-token ...]"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from metasim.app import app, sim


def main() -> None:
    ap = argparse.ArgumentParser(description="Independent Meta simulator and WhatsApp test frontend")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SIM_PORT", "9100")))
    ap.add_argument("--host", default=os.environ.get("SIM_HOST", "127.0.0.1"))
    ap.add_argument("--webhook-url", default=os.environ.get("SIM_WEBHOOK_URL"))
    ap.add_argument("--app-secret", default=os.environ.get("SIM_APP_SECRET"))
    ap.add_argument("--verify-token", default=os.environ.get("SIM_VERIFY_TOKEN"))
    ap.add_argument("--state-file", default=os.environ.get("SIM_STATE_FILE"), help="keep the phones, numbers and chats here, so a restart does not wipe them")
    a = ap.parse_args()
    if a.webhook_url:
        sim.settings.webhook_url = a.webhook_url
    if a.app_secret:
        sim.settings.app_secret = a.app_secret
    if a.verify_token:
        sim.settings.verify_token = a.verify_token
    if a.state_file:
        sim.state_file = Path(a.state_file)
        sim.state_file.parent.mkdir(parents=True, exist_ok=True)
        print("restored the phones, numbers and chats from " + str(sim.state_file) if sim.load_state() else "no saved state yet: starting with the demo phones", flush=True)
    print(f"meta-sim UI  http://{a.host}:{a.port}/   Graph base (META_GRAPH_BASE) http://{a.host}:{a.port}   webhook -> {sim.settings.webhook_url}", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
