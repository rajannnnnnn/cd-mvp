"""Per-action load test: what each thing an owner does costs, in latency, throughput and database work.

    python -m loadtest.actions [--base http://127.0.0.1:8000] [--requests 200] [--concurrency 8] [--db-url postgresql://postgres@127.0.0.1:5432/salesai]

Run it against an API-only process (`python -m salesai api`) with no workers or scheduler running, so the database counters belong to the
actions and not to background polling. It signs in as the seeded demo owner through the real login, then for every action sends --requests
real HTTP calls at --concurrency and records:

  client side   p50 / p95 / p99 / max latency, throughput (requests per second), errors
  database side queries per request, database time per request (pg_stat_statements), rows and buffers touched, transactions per request,
                and the most expensive statement

--db-url needs a role that may read pg_stat_statements (the `postgres` superuser on a developer machine). Results are printed as a table and
written to loadtest/results/actions-c<concurrency>.json and .md. Absolute numbers depend on the machine; compare actions with each other."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

OUT = Path(__file__).parent / "results"
V1 = "/api/v1"
OWNER = "+919999900001"   # the seeded demo owner; override with --owner


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


async def login(c: httpx.AsyncClient, phone: str) -> dict[str, str]:
    r = await c.post(f"{V1}/auth/otp/request", json={"phone": phone})
    r.raise_for_status()
    inbox = (await c.get(f"{V1}/sim/inbox", params={"phone": phone})).json()
    code = re.search(r"\b(\d{6})\b", [m for m in inbox if m["template_name"] == "otp_login"][-1]["body"]).group(1)
    r = await c.post(f"{V1}/auth/otp/verify", json={"phone": phone, "code": code})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def db_counters(conn: psycopg.Connection) -> dict:
    return conn.execute(
        """SELECT xact_commit + xact_rollback AS xacts, tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted, blks_read, blks_hit
           FROM pg_stat_database WHERE datname = current_database()""").fetchone()


def db_statements(conn: psycopg.Connection) -> dict:
    rows = conn.execute(
        """SELECT query, calls, total_exec_time, rows, shared_blks_hit + shared_blks_read AS blocks
           FROM monitoring.pg_stat_statements WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
             AND query NOT ILIKE '%pg_stat_%' AND query NOT ILIKE '%monitoring.%' AND query NOT ILIKE 'SET %' AND query NOT ILIKE 'BEGIN%' AND query NOT ILIKE 'COMMIT%' AND query NOT ILIKE 'ROLLBACK%'
           ORDER BY total_exec_time DESC""").fetchall()
    top = rows[0] if rows else None
    return {"queries": sum(int(r["calls"]) for r in rows), "ms": sum(float(r["total_exec_time"]) for r in rows), "rows": sum(int(r["rows"]) for r in rows),
            "blocks": sum(int(r["blocks"]) for r in rows),
            "top": (re.sub(r"\s+", " ", top["query"])[:110] if top else ""), "top_ms": float(top["total_exec_time"]) if top else 0.0}


async def measure(name: str, n: int, conc: int, call, conn: psycopg.Connection, warm: int = 5) -> dict:
    """call(i) -> awaitable returning the httpx response. The first `warm` calls are not counted."""
    for i in range(warm):
        await call(-1 - i)
    conn.execute("SELECT monitoring.pg_stat_statements_reset()")
    before = db_counters(conn)
    lat: list[float] = []
    errors = 0
    sem = asyncio.Semaphore(conc)

    async def one(i: int) -> None:
        nonlocal errors
        async with sem:
            t = time.perf_counter()
            try:
                r = await call(i)
                if r.status_code >= 400:
                    errors += 1
            except Exception:  # noqa: BLE001
                errors += 1
            lat.append((time.perf_counter() - t) * 1000)

    t0 = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(n)))
    wall = time.perf_counter() - t0
    after = db_counters(conn)
    st = db_statements(conn)
    d = {k: int(after[k] - before[k]) for k in before}
    return {
        "action": name, "requests": n, "concurrency": conc, "errors": errors, "throughput_rps": round(n / wall, 1),
        "p50_ms": round(pct(lat, .5), 1), "p95_ms": round(pct(lat, .95), 1), "p99_ms": round(pct(lat, .99), 1), "max_ms": round(max(lat), 1),
        "db_queries_per_req": round(st["queries"] / n, 1), "db_ms_per_req": round(st["ms"] / n, 2), "db_rows_per_req": round(st["rows"] / n, 1),
        "db_blocks_per_req": round(st["blocks"] / n, 1), "db_xacts_per_req": round(d["xacts"] / n, 1),
        "db_written_per_req": round((d["tup_inserted"] + d["tup_updated"] + d["tup_deleted"]) / n, 1), "top_statement": st["top"],
    }


async def main(a: argparse.Namespace) -> None:
    OUT.mkdir(exist_ok=True)
    conn = psycopg.connect(a.db_url, row_factory=dict_row, autocommit=True)
    conn.execute("CREATE SCHEMA IF NOT EXISTS monitoring")      # not in public: the migration tool revokes on public functions and may not touch the extension's
    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements SCHEMA monitoring")
    n, cc = a.requests, a.concurrency
    results: list[dict] = []
    async with httpx.AsyncClient(base_url=a.base, timeout=30, limits=httpx.Limits(max_connections=cc * 2)) as c:
        h = await login(c, a.owner)
        get = lambda path: (lambda i: c.get(f"{V1}{path}", headers=h))          # noqa: E731
        conv = (await c.get(f"{V1}/conversations", headers=h, params={"limit": 1})).json()["items"][0]["id"]
        run = uuid.uuid4().hex[:6]
        created: list[str] = []
        pending = {"p": []}

        async def new_product(i: int):
            r = await c.post(f"{V1}/products", headers=h, json={"name": f"Load {run} {i}", "category": "load-test", "active": True, "attributes": {}, "variants": [{
                "name": "Standard", "availability": "in_stock", "stock_qty": 5, "is_default": True, "active": True, "attributes": {},
                "policy": {"disclosure": "fixed", "currency": "INR", "list_price": "999", "negotiable": False, "clear_floor": False, "ai_may_negotiate": False,
                           "concession_steps": 0, "concession_requires": [], "round_to": "1"}}]})
            if r.status_code == 201:
                created.append(r.json()["id"])
            return r

        async def new_offer(i: int):
            r = await c.post(f"{V1}/offers", headers=h, json={"name": f"Load offer {run} {i}", "kind": "percent", "value": "5", "conditions": {}, "may_cross_floor": False, "active": True})
            if r.status_code == 201:
                pending["p"].append(r.json()["id"])
            return r

        reads = [
            ("who am I  (GET /auth/me)", get("/auth/me")),
            ("shop settings  (GET /business)", get("/business")),
            ("products list, 100  (GET /products)", lambda i: c.get(f"{V1}/products", headers=h, params={"limit": 100})),
            ("offers list  (GET /offers)", get("/offers")),
            ("chats list, 30  (GET /conversations)", lambda i: c.get(f"{V1}/conversations", headers=h, params={"limit": 30})),
            ("one chat with history  (GET /conversations/{id})", get(f"/conversations/{conv}")),
            ("home dashboard numbers  (GET /metrics/overview)", get("/metrics/overview")),
            ("analytics, 30 days  (GET /analytics)", lambda i: c.get(f"{V1}/analytics", headers=h, params={"days": 30})),
            ("billing  (GET /billing)", get("/billing")),
            ("support tickets list  (GET /support/tickets)", get("/support/tickets")),
            ("price ladder preview  (POST /pricing/ladder)", lambda i: c.post(f"{V1}/pricing/ladder", headers=h, json={"list_price": "7650", "floor_price": "6900", "concession_steps": 3, "round_to": "10"})),
        ]
        for name, call in reads:
            results.append(await measure(name, n, cc, call, conn))
            print(f"  {name}: p50 {results[-1]['p50_ms']} ms, {results[-1]['throughput_rps']} req/s", flush=True)

        # writes: create -> switch off/on -> delete, on products created here
        results.append(await measure("add a product  (POST /products)", n, cc, new_product, conn, warm=0))
        ids = list(created)
        results.append(await measure("switch a product off  (PATCH /products/{id})", len(ids), cc,
                                     lambda i: c.patch(f"{V1}/products/{ids[i % len(ids)]}", headers=h, json={"active": False}), conn, warm=0))
        results.append(await measure("delete a product  (DELETE /products/{id})", len(ids), cc,
                                     lambda i: c.delete(f"{V1}/products/{ids[i]}", headers=h), conn, warm=0))
        results.append(await measure("add an offer  (POST /offers)", n, cc, new_offer, conn, warm=0))
        oids = list(pending["p"])
        results.append(await measure("delete an offer  (DELETE /offers/{id})", len(oids), cc,
                                     lambda i: c.delete(f"{V1}/offers/{oids[i]}", headers=h), conn, warm=0))
        for r in results[-5:]:
            print(f"  {r['action']}: p50 {r['p50_ms']} ms, {r['throughput_rps']} req/s", flush=True)

    (OUT / f"actions{a.tag}-c{cc}.json").write_text(json.dumps(results, indent=1))
    cols = ("action", "throughput_rps", "p50_ms", "p95_ms", "p99_ms", "errors", "db_queries_per_req", "db_ms_per_req", "db_xacts_per_req", "db_rows_per_req", "db_written_per_req")
    head = ["Action", "req/s", "p50 ms", "p95 ms", "p99 ms", "errors", "DB queries/req", "DB ms/req", "DB tx/req", "DB rows/req", "rows written/req"]
    md = [f"Per-action load test: {n} requests per action, concurrency {cc}, {time.strftime('%Y-%m-%d %H:%M')}.", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    md += ["| " + " | ".join(str(r[k]) for k in cols) + " |" for r in results]
    md += ["", "Most expensive statement per action:", ""] + [f"- **{r['action']}**: `{r['top_statement']}`" for r in results]
    (OUT / f"actions{a.tag}-c{cc}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--requests", type=int, default=200)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--db-url", default="postgresql://postgres@127.0.0.1:5432/salesai_dev")
    p.add_argument("--owner", default=OWNER, help="phone of the shop owner to sign in as")
    p.add_argument("--tag", default="", help="suffix for the result files")
    asyncio.run(main(p.parse_args()))
