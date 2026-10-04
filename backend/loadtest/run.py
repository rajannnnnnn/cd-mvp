"""Load tests for the non-functional requirements (docs/PRD.md NFR-1, NFR-2, NFR-5).

    python -m loadtest.run webhook        # NFR-1: acknowledge signed webhooks in < 300 ms p99, nothing lost
    python -m loadtest.run conversations  # NFR-2/NFR-5: many shops and customers talking at once, end to end

Both talk to a RUNNING stack (backend on --base, default http://127.0.0.1:8000; the database via the SYSTEM_DATABASE_URL /
DATABASE settings in the environment) and use the simulated WhatsApp network, so they need no vendor account. Results are
printed and written to loadtest/results/<name>.json. They measure the system with the language-model stand-in (zero model
latency); with a real model add its latency to NFR-2 (the report separates queueing/processing from model time via turns.latency_ms)."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from salesai.config import get_settings
from salesai.modules.channels.simulator import build_message_payload, signed

OUT = Path(__file__).parent / "results"


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


def numbers_rows(url: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as conn:
        return conn.execute("SELECT phone_number_id, display_phone FROM whatsapp_numbers WHERE channel='simulator'").fetchall()


def number(conn: psycopg.Connection) -> dict:
    row = conn.execute("SELECT n.phone_number_id, n.display_phone, b.id AS business_id FROM whatsapp_numbers n JOIN businesses b ON b.id=n.business_id "
                       "WHERE n.channel='simulator' AND n.status='connected' ORDER BY n.created_at LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("no simulated number found: run `python -m salesai.seed` first")
    return row


async def webhook(args: argparse.Namespace) -> dict:
    s = get_settings()
    with psycopg.connect(s.system_database_url, row_factory=dict_row) as conn:
        n = number(conn)
    url = f"{args.base}/webhooks/whatsapp"
    run = uuid.uuid4().hex[:8]                       # marks this run's messages so they can be counted exactly
    lat: list[float] = []
    codes: dict[int, int] = {}
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=args.concurrency * 2)) as client:
        async def one(i: int) -> None:
            phone = f"+9196{(i % 5000):08d}"
            payload = build_message_payload(n["phone_number_id"], n["display_phone"], phone, f"load {run}:{i}", kind="text", name="Load")
            body, sig = signed(s.meta_app_secret, payload)
            async with sem:
                t0 = time.perf_counter()
                r = await client.post(url, content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
                lat.append((time.perf_counter() - t0) * 1000)
                codes[r.status_code] = codes.get(r.status_code, 0) + 1
        t0 = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(args.requests)))
        wall = time.perf_counter() - t0
    # durability (INV-6/NFR-3): every acknowledged message is stored; wait for the pipeline to drain
    deadline = time.time() + args.drain_s
    stored = 0
    with psycopg.connect(s.system_database_url, row_factory=dict_row) as conn:
        while time.time() < deadline:
            stored = conn.execute("SELECT count(*) AS c FROM messages WHERE business_id=%s AND direction='in' AND body LIKE %s",
                                  (n["business_id"], f"load {run}:%")).fetchone()["c"]
            if stored >= codes.get(200, 0):
                break
            await asyncio.sleep(1)
        dead = conn.execute("SELECT count(*) AS c FROM jobs WHERE queue='inbound.events' AND status='dead'").fetchone()["c"]
    res = {"test": "webhook_ack", "requests": args.requests, "concurrency": args.concurrency, "wall_s": round(wall, 2),
           "throughput_rps": round(args.requests / wall, 1), "status_codes": codes,
           "ack_ms": {"p50": round(pct(lat, .5), 1), "p95": round(pct(lat, .95), 1), "p99": round(pct(lat, .99), 1), "max": round(max(lat), 1)},
           "messages_stored": stored, "acknowledged_200": codes.get(200, 0), "dead_letters_inbound": dead,
           "NFR-1_p99_under_300ms": pct(lat, .99) < 300, "NFR-3_nothing_lost": stored >= codes.get(200, 0)}
    return res


async def conversations(args: argparse.Namespace) -> dict:
    s = get_settings()
    base = args.base.rstrip("/")
    with psycopg.connect(s.system_database_url, row_factory=dict_row) as conn:
        nums = conn.execute("SELECT n.display_phone, n.business_id FROM whatsapp_numbers n WHERE n.channel='simulator' AND n.status='connected'").fetchall()
        started = conn.execute("SELECT now() AS t").fetchone()["t"]
    if not nums:
        raise SystemExit("no simulated numbers: run `python -m salesai.seed` first")
    scripts = ["hi", "price of the first product you have?", "thoda kam karo na", "ok I will take it"]
    sent = 0
    sem = asyncio.Semaphore(args.concurrency)
    url = f"{base}/webhooks/whatsapp"
    pn = {n["display_phone"]: n["phone_number_id"] for n in numbers_rows(s.system_database_url)}

    async def customer(i: int, client: httpx.AsyncClient) -> None:
        nonlocal sent
        num = nums[i % len(nums)]
        phone = f"+9195{uuid.uuid4().int % 10**8:08d}"
        async with sem:
            for text in scripts:
                payload = build_message_payload(pn[num["display_phone"]], num["display_phone"], phone, text, kind="text", name=f"Load {i}")
                body, sig = signed(s.meta_app_secret, payload)
                r = await client.post(url, content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
                r.raise_for_status()
                sent += 1
                await asyncio.sleep(args.gap_s)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=10) as client:
        await asyncio.gather(*(customer(i, client) for i in range(args.customers)))
    deadline = time.time() + args.drain_s
    with psycopg.connect(s.system_database_url, row_factory=dict_row) as conn:
        while time.time() < deadline:
            pending = conn.execute("SELECT count(*) AS c FROM jobs WHERE status IN ('pending','running') AND queue IN ('inbound.events','conversation.turns','outbound.actions')").fetchone()["c"]
            if pending == 0:
                break
            await asyncio.sleep(1)
        wall = time.perf_counter() - t0
        turns = conn.execute("SELECT latency_ms FROM turns WHERE created_at >= %s AND latency_ms IS NOT NULL", (started,)).fetchall()
        # end of customer's last unanswered message to first outbound text of the same conversation
        replies = conn.execute("""
            SELECT extract(epoch FROM (o.sent_at - i.created_at)) * 1000 AS ms
            FROM messages i JOIN LATERAL (SELECT sent_at FROM messages o WHERE o.conversation_id=i.conversation_id AND o.direction='out' AND o.sent_at > i.created_at ORDER BY o.sent_at LIMIT 1) o ON true
            WHERE i.direction='in' AND i.created_at >= %s""", (started,)).fetchall()
        dead = conn.execute("SELECT count(*) AS c FROM jobs WHERE status='dead' AND finished_at >= %s", (started,)).fetchone()["c"]
        failed = conn.execute("SELECT count(*) AS c FROM messages WHERE direction='out' AND status='failed' AND created_at >= %s", (started,)).fetchone()["c"]
    tl = [t["latency_ms"] for t in turns]
    rl = [float(r["ms"]) for r in replies if r["ms"] is not None]
    return {"test": "conversations", "businesses": len(nums), "customers": args.customers, "messages_sent": sent, "wall_s": round(wall, 1),
            "turn_pipeline_ms": {"p50": round(statistics.median(tl)) if tl else 0, "p95": round(pct(tl, .95))},
            "message_to_first_reply_ms": {"p50": round(statistics.median(rl)) if rl else 0, "p95": round(pct(rl, .95))},
            "dead_letters": dead, "failed_sends": failed,
            "note": "includes intentional human-like pacing unless the businesses are configured fast; turn_pipeline_ms excludes pacing"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("test", choices=["webhook", "conversations"])
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--requests", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--customers", type=int, default=60)
    ap.add_argument("--gap-s", type=float, default=2.0)
    ap.add_argument("--drain-s", type=int, default=120)
    args = ap.parse_args()
    res = asyncio.run(webhook(args) if args.test == "webhook" else conversations(args))
    OUT.mkdir(exist_ok=True)
    (OUT / f"{args.test}.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
