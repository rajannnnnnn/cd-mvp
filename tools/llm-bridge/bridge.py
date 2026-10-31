#!/usr/bin/env python3
"""LLM bridge: a local server that speaks the Anthropic Messages wire format (POST /v1/messages) and answers every request with a FRESH,
independent Claude CLI process.

Why it exists: to exercise the product's real Anthropic provider end to end without an API key, and with no mock inside the product. The
product only needs ANTHROPIC_BASE_URL pointed here; it cannot tell the difference from api.anthropic.com.

Independence rules (do not break them):
  * This folder never imports anything from the product (`salesai`), and the product never imports anything from here.
  * Each request starts a new process in a new EMPTY temp directory, with no session persistence and no tools. The model sees only the
    system prompt and messages the product sent, exactly like a stateless API call: no repo, no CLAUDE.md, no earlier calls.
  * Every request and reply is appended to a JSONL log so a human (or Claude) can read the whole conversation afterwards.

Uses only the standard library. Run:  python bridge.py --port 9200 --log-dir ./logs
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

CLAUDE = shutil.which("claude") or "claude"
DEFAULT_MODEL = "sonnet"
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")

_log_lock = threading.Lock()


def text_of(content: Any) -> str:
    """Flatten an Anthropic content value (string or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for b in content or []:
        t = b.get("type")
        if t == "text":
            parts.append(b.get("text", ""))
        elif t == "tool_use":
            parts.append("[tool call] " + json.dumps({"name": b.get("name"), "input": b.get("input")}, ensure_ascii=False))
        elif t == "tool_result":
            parts.append("[tool result] " + text_of(b.get("content")))
        else:
            parts.append(json.dumps(b, ensure_ascii=False))
    return "\n".join(parts)


def render_prompt(messages: list[dict[str, Any]]) -> str:
    if len(messages) == 1 and messages[0].get("role") == "user":
        return text_of(messages[0].get("content"))
    out = ["Below is the conversation so far. Reply with the next assistant message only.", ""]
    for m in messages:
        out += [f"[{m.get('role', 'user')}]", text_of(m.get("content")), ""]
    return "\n".join(out)


def pick_model(requested: str | None) -> list[str]:
    """Try the exact model id first, then a family alias (the CLI accepts both)."""
    r = (requested or "").lower()
    alias = "haiku" if "haiku" in r else "opus" if "opus" in r else "sonnet" if "sonnet" in r else DEFAULT_MODEL
    return [requested, alias] if requested and requested != alias else [alias]


def strip_fences(s: str) -> str:
    s = s.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    return m.group(1) if m else s


def run_cli(system: str, prompt: str, schema: dict[str, Any] | None, model: str, timeout_s: int) -> dict[str, Any]:
    work = tempfile.mkdtemp(prefix="llm-bridge-")
    try:
        sys_file = Path(work, "system.txt")
        sys_file.write_text(system or "You are a stateless function. Use only the user message.", encoding="utf-8")
        cmd = [CLAUDE, "-p", "--system-prompt-file", str(sys_file), "--no-session-persistence", "--model", model,
               "--output-format", "json", "--tools", "", "--disable-slash-commands", "--strict-mcp-config"]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=work, env=env, timeout=timeout_s)  # noqa: S603
        if p.returncode != 0 and not p.stdout.strip():
            raise RuntimeError(f"claude exited {p.returncode}: {p.stderr.strip()[:400]}")
        return json.loads(p.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def answer(body: dict[str, Any], timeout_s: int) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Returns (reply_text, usage, debug)."""
    system = body.get("system")
    system = "\n\n".join(b.get("text", "") for b in system) if isinstance(system, list) else (system or "")
    schema = ((body.get("output_config") or {}).get("format") or {}).get("schema")
    prompt = render_prompt(body.get("messages") or [])
    if schema is not None:
        prompt += "\n\nRespond with ONLY a single JSON object that satisfies the required schema. No prose, no code fences."
    last_err = ""
    for model in pick_model(body.get("model")):
        for attempt in (1, 2):
            p = prompt if attempt == 1 else prompt + f"\n\nYour previous reply was not valid ({last_err}). Reply again with ONLY the JSON object."
            try:
                res = run_cli(system, p, schema, model, timeout_s)
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                break                                         # try the next model alias
            if res.get("is_error"):
                last_err = str(res.get("result"))[:300]
                break
            text = res.get("structured_output")
            text = json.dumps(text, ensure_ascii=False) if text is not None else strip_fences(str(res.get("result") or ""))
            if schema is not None:
                try:
                    json.loads(text)
                except ValueError as e:
                    last_err = f"invalid JSON: {e}"
                    continue                                  # one retry with the error shown
            u = res.get("usage") or {}
            usage = {"input_tokens": int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)),
                     "output_tokens": int(u.get("output_tokens", 0))}
            return text, usage, {"model_used": model, "attempt": attempt, "cost_usd": res.get("total_cost_usd"), "api_ms": res.get("duration_api_ms")}
    raise RuntimeError(last_err or "no answer")


class Handler(BaseHTTPRequestHandler):
    server_version = "llm-bridge/1"
    log_dir: Path
    sem: threading.Semaphore
    timeout_s: int

    def _send(self, code: int, obj: dict[str, Any]) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200 if self.path in ("/healthz", "/") else 404, {"status": "ok"} if self.path in ("/healthz", "/") else {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/v1/messages":
            return self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": "only POST /v1/messages"}})
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0")) or 0) or b"{}")
        rid = "msg_" + uuid.uuid4().hex[:24]
        t0 = time.time()
        rec: dict[str, Any] = {"id": rid, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": body.get("model"), "system": body.get("system"),
                               "messages": body.get("messages"), "schema": ((body.get("output_config") or {}).get("format") or {}).get("schema")}
        try:
            with self.sem:
                text, usage, dbg = answer(body, self.timeout_s)
            rec |= {"reply": text, "usage": usage, "debug": dbg, "seconds": round(time.time() - t0, 2)}
            self._send(200, {"id": rid, "type": "message", "role": "assistant", "model": body.get("model") or DEFAULT_MODEL,
                             "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None, "usage": usage})
        except Exception as e:  # noqa: BLE001
            rec |= {"error": f"{type(e).__name__}: {e}", "seconds": round(time.time() - t0, 2)}
            self._send(529, {"type": "error", "error": {"type": "overloaded_error", "message": str(e)[:300]}})
        finally:
            with _log_lock, open(self.log_dir / "calls.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[bridge] " + (fmt % args) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=9200)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log-dir", default="./logs")
    ap.add_argument("--concurrency", type=int, default=2, help="parallel CLI processes (each needs ~200 MB of memory)")
    ap.add_argument("--timeout", type=int, default=170)
    a = ap.parse_args()
    log_dir = Path(a.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    Handler.log_dir, Handler.sem, Handler.timeout_s = log_dir, threading.Semaphore(a.concurrency), a.timeout
    print(f"llm-bridge on http://{a.host}:{a.port}  log={log_dir.resolve() / 'calls.jsonl'}  cli={CLAUDE}", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
