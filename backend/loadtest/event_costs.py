"""What each business event costs the database, on production-sized data.

    python -m loadtest.event_costs --url postgresql://postgres:PW@127.0.0.1:5433/salesai_scale [--runs 300]

The database must be migrated and filled by `python -m loadtest.scale_data` (scratch database only). For each business event this runs the
product's own SQL, in the order the code runs it, as the same database login the product uses (the shop's limited login with row-level
security on, `app.business_id` set, exactly like `db.tenant()`), against a random shop and a random chat each time. Writes are rolled back,
so the data does not grow and every run sees the same size. Per event it prints the number of statements, then p50 / p95 / max milliseconds
for the whole event (client side, one connection, no network hop beyond loopback), and the plan of every read once: whether it used an
index or read a whole table, and how many pages it touched.

It measures the database only: no model, no WhatsApp, no pacing delay. Compare events with each other; absolute numbers depend on the machine."""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

OUT = Path(__file__).parent / "results"


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


class Env:
    """One connection per login, one transaction per event run, rolled back at the end."""

    def __init__(self, url: str, role: str, password: str):
        base = url.split("@", 1)[1]
        self.conn = psycopg.connect(f"postgresql://app_{role}:{password}@{base}", row_factory=dict_row, autocommit=False)
        self.statements = 0
        self.plans: dict[str, dict[str, Any]] = {}
        self.explain_next = False

    def start(self, business_id: uuid.UUID | None = None) -> None:
        self.statements = 0
        if business_id is not None:
            self.conn.execute("SELECT set_config('app.business_id', %s, true)", (str(business_id),))

    def q(self, name: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        self.statements += 1
        if self.explain_next and name not in self.plans and sql.lstrip().upper().startswith(("SELECT", "WITH")):
            row = self.conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).fetchone()
            plan = row["QUERY PLAN"][0]
            nodes: list[str] = []

            def walk(n: dict[str, Any]) -> None:
                nodes.append(f"{n['Node Type']}" + (f" on {n['Relation Name']}" if "Relation Name" in n else ""))
                for ch in n.get("Plans", []):
                    walk(ch)
            walk(plan["Plan"])
            self.plans[name] = {"ms": round(plan["Execution Time"], 2), "pages": plan["Plan"].get("Shared Hit Blocks", 0) + plan["Plan"].get("Shared Read Blocks", 0),
                                "seq_scans": [n for n in nodes if n.startswith("Seq Scan")], "nodes": nodes[:6]}
        cur = self.conn.execute(sql, params)
        return cur.fetchall() if cur.description else []

    def rollback(self) -> None:
        self.conn.rollback()


def sample(admin_url: str) -> dict[str, list[Any]]:
    with psycopg.connect(admin_url, row_factory=dict_row) as c:
        convs = c.execute("""SELECT cv.id, cv.business_id, cv.customer_id, cv.whatsapp_number_id, cv.version, cu.wa_id, n.phone_number_id
                              FROM conversations cv JOIN customers cu ON cu.id=cv.customer_id JOIN whatsapp_numbers n ON n.id=cv.whatsapp_number_id
                              ORDER BY random() LIMIT 2000""").fetchall()
        variants = c.execute("SELECT variant_id, business_id FROM pricing_policies ORDER BY random() LIMIT 2000").fetchall()
    random.shuffle(convs)
    return {"convs": convs, "variants": variants}


# ------------------------------------------------------------------------------------------------------ the events (the code's own SQL)
def ev_webhook_in(e: Env, cv: dict[str, Any]) -> None:
    """A customer's message reaches the gateway: find the shop's number, keep the raw request, queue the event."""
    e.start()
    e.q("resolve number by phone_number_id", "SELECT id, business_id, status FROM whatsapp_numbers WHERE phone_number_id=%s", (cv["phone_number_id"],))
    e.q("keep raw webhook", "INSERT INTO webhook_events (signature_valid, payload, raw_body, signature) VALUES (true, %s, %s, %s) RETURNING id",
        (json.dumps({"entry": []}), b"x" * 1200, "sha256=abc"))


def ev_inbound_stored(e: Env, cv: dict[str, Any]) -> None:
    """The gateway's reader turns it into a clean event; the agent side stores the message on the customer's chat."""
    e.start(cv["business_id"])
    e.q("customer by wa_id", "SELECT id FROM customers WHERE wa_id=%s", (cv["wa_id"],))
    e.q("chat by customer+number (lock)", "SELECT * FROM conversations WHERE customer_id=%s AND whatsapp_number_id=%s FOR UPDATE", (cv["customer_id"], cv["whatsapp_number_id"]))
    e.q("latest inbound time", "SELECT max(wa_timestamp) AS t FROM messages WHERE conversation_id=%s AND direction='in'", (cv["id"],))
    e.q("store message", """INSERT INTO messages (business_id, conversation_id, wa_message_id, direction, sender, kind, body, status, gap_ms, answered, wa_timestamp)
                            VALUES (%s,%s,%s,'in','customer','text','Is this available in blue? What is the best price?','received',1200,false,now())
                            ON CONFLICT (wa_message_id) DO NOTHING RETURNING id""", (cv["business_id"], cv["id"], "wamid." + uuid.uuid4().hex))
    e.q("update chat", "UPDATE conversations SET last_inbound_at=now(), updated_at=now(), version=version+1 WHERE id=%s", (cv["id"],))


def ev_agent_context(e: Env, cv: dict[str, Any]) -> None:
    """The agent wakes for a chat and reads everything it needs to answer (modules/agent/context.py, statement for statement)."""
    e.start(cv["business_id"])
    bid, cid = cv["business_id"], cv["id"]
    e.q("shop", "SELECT * FROM businesses WHERE id=%s", (bid,))
    e.q("chat", "SELECT * FROM conversations WHERE id=%s", (cid,))
    e.q("customer", "SELECT * FROM customers WHERE id=%s", (cv["customer_id"],))
    e.q("number", "SELECT id, business_id, channel, phone_number_id, display_phone, verified_name, status, quality_rating, coexistence FROM whatsapp_numbers WHERE id=%s", (cv["whatsapp_number_id"],))
    e.q("unanswered messages", "SELECT id, kind, body, wa_message_id, created_at FROM messages WHERE conversation_id=%s AND direction='in' AND NOT answered ORDER BY created_at, id", (cid,))
    e.q("last 20 messages", "SELECT id, sender, body, kind, created_at FROM messages WHERE conversation_id=%s AND (answered OR direction='out') ORDER BY created_at DESC, id DESC LIMIT 20", (cid,))
    n = e.q("catalog size", "SELECT count(*) AS n FROM catalog_public")[0]["n"]
    toks = ["blue", "saree", "price"]
    if n > 30:
        e.q("catalog search (trigram)", """SELECT *, (SELECT coalesce(sum(word_similarity(t, hay)), 0) FROM unnest(%s::text[]) t) AS score
              FROM (SELECT cp.*, lower(cp.product_name || ' ' || coalesce(cp.description,'') || ' ' || coalesce(cp.category,'') || ' ' || cp.variant_name) AS hay FROM catalog_public cp) s
              WHERE EXISTS (SELECT 1 FROM unnest(%s::text[]) t WHERE word_similarity(t, hay) > 0.4) ORDER BY score DESC LIMIT 8""", (toks, toks))
    e.q("style examples", "SELECT situation, customer_text, owner_reply FROM style_examples ORDER BY created_at DESC, id DESC LIMIT 8")
    e.q("repeat customer?", "SELECT 1 FROM deals d JOIN conversations cv ON cv.id = d.conversation_id WHERE cv.customer_id=%s AND d.status='won' LIMIT 1", (cv["customer_id"],))
    e.q("AI replied before?", "SELECT 1 FROM messages WHERE conversation_id=%s AND direction='out' AND sender='ai' LIMIT 1", (cid,))
    e.q("offers", "SELECT * FROM offers ORDER BY created_at DESC")


def ev_pricing(e: Env, cv: dict[str, Any], var: dict[str, Any]) -> None:
    """The pricing step reads a variant's rules and its floor (the floor login only), and the chat's negotiation."""
    e.start(var["business_id"])
    e.q("pricing rules", "SELECT * FROM pricing_policies WHERE variant_id=%s", (var["variant_id"],))
    e.q("negotiation of this chat", "SELECT * FROM negotiations WHERE conversation_id=%s ORDER BY updated_at DESC LIMIT 1", (cv["id"],))


def ev_turn_recorded(e: Env, cv: dict[str, Any]) -> None:
    """The agent's decision is recorded on the chat."""
    e.start(cv["business_id"])
    e.q("turn record", """INSERT INTO turns (business_id, conversation_id, conversation_version, status, model, input_tokens, output_tokens, cost_micros, latency_ms, completed_at)
                          VALUES (%s,%s,%s,'sent','m',900,120,50,40,now()) RETURNING id""", (cv["business_id"], cv["id"], cv["version"]))
    e.q("mark answered", "UPDATE messages SET answered=true WHERE conversation_id=%s AND direction='in' AND NOT answered", (cv["id"],))
    e.q("chat state", "UPDATE conversations SET state='idle', last_outbound_at=now(), updated_at=now(), version=version+1 WHERE id=%s", (cv["id"],))


def ev_reply_sent(e: Env, cv: dict[str, Any]) -> None:
    """The reply goes out: the message is stored and its delivery state is updated as Meta reports it."""
    e.start(cv["business_id"])
    r = e.q("store reply", """INSERT INTO messages (business_id, conversation_id, wa_message_id, direction, sender, kind, body, status, answered, sent_at)
                              VALUES (%s,%s,%s,'out','ai','text','Yes, in blue it is Rs 1,450. I can do 1,400 for you.','sent',true,now()) RETURNING id""",
            (cv["business_id"], cv["id"], "wamid." + uuid.uuid4().hex))
    wa = e.q("find by wa id", "SELECT id, status, conversation_id, sender FROM messages WHERE wa_message_id=(SELECT wa_message_id FROM messages WHERE id=%s) FOR UPDATE", (r[0]["id"],))
    e.q("delivered", "UPDATE messages SET status='delivered', delivered_at=now() WHERE id=%s", (wa[0]["id"],))
    e.q("read", "UPDATE messages SET status='read', read_at=now() WHERE id=%s", (wa[0]["id"],))


def ev_handoff(e: Env, cv: dict[str, Any]) -> None:
    """The customer needs the owner: a hand-off is opened, the chat is paused and the owner is messaged."""
    e.start(cv["business_id"])
    e.q("open handoff", "INSERT INTO handoffs (business_id, conversation_id, reason, status) VALUES (%s,%s,'customer_asked_human','open') RETURNING id", (cv["business_id"], cv["id"]))
    e.q("pause chat", "UPDATE conversations SET ai_paused_until=now()+interval '2 hours', ai_paused_reason='handoff', version=version+1 WHERE id=%s", (cv["id"],))
    bu = e.q("owner of the shop", "SELECT id FROM business_users WHERE role='owner' LIMIT 1")[0]["id"]
    e.q("owner message", "INSERT INTO owner_messages (business_id, business_user_id, direction, kind, purpose, body, status) VALUES (%s,%s,'out','text','handoff_alert','A customer needs you','queued')", (cv["business_id"], bu))


def ev_deal(e: Env, cv: dict[str, Any]) -> None:
    """An order or a store visit is captured."""
    e.start(cv["business_id"])
    e.q("capture deal", "INSERT INTO deals (business_id, conversation_id, kind, value, status) VALUES (%s,%s,'order',1400,'pending') RETURNING id", (cv["business_id"], cv["id"]))
    e.q("chat stage", "UPDATE conversations SET lead_stage='ready_to_buy', version=version+1 WHERE id=%s", (cv["id"],))


def ev_queue(e: Env, cv: dict[str, Any]) -> None:
    """Every hand-off between parts is a job: publish it, a worker claims the oldest due one, then marks it done (queue/postgres.py)."""
    e.start()
    e.q("publish job", """INSERT INTO jobs (queue, kind, business_id, ordering_key, payload, run_at, max_attempts) VALUES ('conversation.events','channel_event',%s,%s,'{}', now(), 5) RETURNING id""",
        (cv["business_id"], "conv:" + str(cv["id"])))
    rows = e.q("claim: find due jobs", """SELECT j.id, j.queue, j.ordering_key, j.business_id FROM jobs j
        WHERE j.status = 'pending' AND j.run_at <= now() AND j.queue = ANY(%s)
          AND (j.ordering_key IS NULL OR NOT EXISTS (SELECT 1 FROM jobs e WHERE e.queue = j.queue AND e.ordering_key = j.ordering_key AND e.status = 'pending' AND (e.run_at, e.id) < (j.run_at, j.id)))
        ORDER BY j.run_at, j.id LIMIT 400""", (["conversation.events", "conversation.turns"],))
    jid = rows[0]["id"] if rows else None
    e.q("claim: take it", "UPDATE jobs SET status='running', attempts=attempts+1, claim_token=claim_token+1, locked_by='w', lease_until=now()+interval '120 s' WHERE id=(SELECT id FROM jobs WHERE id=%s AND status='pending' FOR UPDATE SKIP LOCKED) RETURNING id", (jid,))
    e.q("complete", "UPDATE jobs SET status='done', finished_at=now(), lease_until=NULL WHERE id=%s AND status='running'", (jid,))


EVENTS: list[tuple[str, str, str, Callable[..., None], bool]] = [
    # (name, what runs it, login, function, needs a variant)
    ("1. Customer message arrives (gateway receive)", "gateway", "system", ev_webhook_in, False),
    ("2. Message stored on the chat (gateway inbound / agent)", "agent", "user", ev_inbound_stored, False),
    ("3. Agent reads everything for a chat", "agent", "user", ev_agent_context, False),
    ("4. Pricing step reads rules + negotiation", "agent", "user", ev_pricing, True),
    ("5. Agent decision recorded", "agent", "user", ev_turn_recorded, False),
    ("6. Reply stored, then delivered and read", "gateway", "user", ev_reply_sent, False),
    ("7. Hand-off to the owner", "agent", "user", ev_handoff, False),
    ("8. Order / visit captured", "agent", "user", ev_deal, False),
    ("9. Queue hand-off: publish, claim, complete", "any", "system", ev_queue, False),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="superuser URL of the scratch database")
    ap.add_argument("--runs", type=int, default=300)
    ap.add_argument("--passwords", default="user_pw,system_pw", help="dev passwords of app_user,app_system (scripts/dev_env.sh)")
    a = ap.parse_args()
    upw, spw = a.passwords.split(",")
    s = sample(a.url)
    envs = {"user": Env(a.url, "user", upw), "system": Env(a.url, "system", spw)}
    results = []
    rt: list[float] = []
    for _ in range(300):
        t0 = time.perf_counter()
        envs["user"].conn.execute("SELECT 1").fetchone()
        rt.append((time.perf_counter() - t0) * 1000)
    envs["user"].rollback()
    rtt = statistics.median(rt)
    print(f"plain round trip to the database on this machine (SELECT 1, p50): {rtt:.2f} ms. Each statement pays this; on the same host or a private network it is about 0.1-0.5 ms.\n")
    for name, part, login, fn, need_var in EVENTS:
        e = envs[login]
        times: list[float] = []
        stmts = 0
        e.plans = {}
        err = ""
        for i in range(a.runs + 5):
            cv = s["convs"][i % len(s["convs"])]
            var = random.choice([v for v in s["variants"] if v["business_id"] == cv["business_id"]] or s["variants"])
            e.explain_next = (i == 2)
            t0 = time.perf_counter()
            try:
                fn(e, cv, var) if need_var else fn(e, cv)
            except Exception as ex:   # noqa: BLE001
                err = f"{type(ex).__name__}: {str(ex)[:140]}"
                e.rollback()
                break
            dt = (time.perf_counter() - t0) * 1000
            stmts = e.statements
            e.rollback()
            if i >= 5:                  # the first five warm the cache and the plans
                times.append(dt)
        row = {"event": name, "run_by": part, "statements": stmts, "error": err,
               "p50_ms": round(statistics.median(times), 2) if times else None, "p95_ms": round(pct(times, .95), 2) if times else None,
               "max_ms": round(max(times), 2) if times else None, "plans": e.plans}
        results.append(row)
        flag = "  !! " + err if err else ""
        if times:
            row["db_only_p50_ms"] = round(max(statistics.median(times) - (stmts + 1) * rtt, 0), 2)     # minus one round trip per statement, plus the tenant switch
        print(f"{name:58s} {stmts:3d} stmts  p50 {row['p50_ms']} ms  (about {row.get('db_only_p50_ms')} ms without round trips)  p95 {row['p95_ms']} ms  max {row['max_ms']} ms{flag}")
        for q, p in e.plans.items():
            scan = f"  SEQ SCAN: {', '.join(p['seq_scans'])}" if p["seq_scans"] else ""
            print(f"      {q:34s} {p['ms']:8.2f} ms  {p['pages']:6d} pages{scan}")
    OUT.mkdir(exist_ok=True)
    (OUT / "event-costs.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwritten to {OUT / 'event-costs.json'}")


if __name__ == "__main__":
    main()
