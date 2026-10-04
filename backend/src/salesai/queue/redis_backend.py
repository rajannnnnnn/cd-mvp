"""Redis queue backend (second implementation of `salesai.queue.base.Queue`; passes the same backend-agnostic
suite as the Postgres one: tests/queue_suite.py).

Every state transition is one Lua script, so each is atomic and workers on any number of machines can race safely:

  job:{id}                 hash with the job (payload, attempts, status, lease, ...)
  pending:{queue}          ZSET  member = zero-padded id, score = run_at (ms)         -> due = score <= now
  okey:{queue}:{key}       ZSET  pending jobs of one ordering key; only its head may be claimed
  okeyrun:{queue}:{key}    string, present while a job of that key is running          -> never two at once
  trun:{queue}             HASH  business id -> running count                          -> per-tenant cap
  leases                   ZSET  running jobs by lease expiry                          -> reaper
  dead:{queue}             ZSET  dead letters by time
  ent:{entity_key}         ZSET  pending jobs of one conversation by version           -> supersession
  biz:{business}           ZSET  every job of a tenant (for hard delete)
  dd:{queue}:{key}         dedupe marker (TTL)

All keys share the hash tag `{salesai:q}` so the scripts also work on a single-slot Redis Cluster. Durability comes
from Redis persistence (AOF `appendonly yes`; see deploy/docker-compose.yml). Time is read inside the scripts from
the Redis server clock, so worker machines need no synchronized clocks.
Redis is standalone or Sentinel in v1; shard by queue prefix if one instance is ever too small."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis

from salesai.queue.base import Job, JobSpec, QueueStats, backoff_s

_DONE_TTL_S = 7 * 24 * 3600
_DEDUPE_TTL_S = 7 * 24 * 3600

# --- shared Lua helpers: remove a job from every index it may be in
_LUA_COMMON = """
local function now_ms()
  local t = redis.call('TIME')
  return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
local function unindex(P, id, member, h)
  -- h = {queue, ordering_key, entity_key, business_id}
  redis.call('ZREM', P..'pending:'..h[1], member)
  if h[2] ~= '' then redis.call('ZREM', P..'okey:'..h[1]..':'..h[2], member) end
  if h[3] ~= '' then redis.call('ZREM', P..'ent:'..h[3], member) end
end
local function release_running(P, id, member, h)
  redis.call('ZREM', P..'leases', member)
  redis.call('SREM', P..'running:'..h[1], member)
  if h[2] ~= '' and redis.call('GET', P..'okeyrun:'..h[1]..':'..h[2]) == tostring(id) then
    redis.call('DEL', P..'okeyrun:'..h[1]..':'..h[2])
  end
  if h[4] ~= '' then
    local n = redis.call('HINCRBY', P..'trun:'..h[1], h[4], -1)
    if n <= 0 then redis.call('HDEL', P..'trun:'..h[1], h[4]) end
  end
end
local function make_pending(P, id, member, h, run_at, version)
  redis.call('ZADD', P..'pending:'..h[1], run_at, member)
  if h[2] ~= '' then redis.call('ZADD', P..'okey:'..h[1]..':'..h[2], run_at, member) end
  if h[3] ~= '' then redis.call('ZADD', P..'ent:'..h[3], version, member) end
end
local function head(P, id)
  return redis.call('HMGET', P..'job:'..id, 'queue', 'ordering_key', 'entity_key', 'business_id', 'entity_version')
end
"""

_PUBLISH = _LUA_COMMON + """
local P = ARGV[1]
local queue, kind, payload, biz, okey, ekey, ever, dedupe, run_at, max_attempts, dttl =
  ARGV[2], ARGV[3], ARGV[4], ARGV[5], ARGV[6], ARGV[7], ARGV[8], ARGV[9], ARGV[10], ARGV[11], ARGV[12]
local t = now_ms()
if dedupe ~= '' then
  if not redis.call('SET', P..'dd:'..queue..':'..dedupe, '1', 'NX', 'EX', dttl) then return 0 end
end
local id = redis.call('INCR', P..'seq')
local member = string.format('%016d', id)
local when = (run_at ~= '') and tonumber(run_at) or t
redis.call('HSET', P..'job:'..id, 'queue', queue, 'kind', kind, 'payload', payload, 'business_id', biz,
  'ordering_key', okey, 'entity_key', ekey, 'entity_version', ever, 'dedupe_key', dedupe,
  'max_attempts', max_attempts, 'attempts', 0, 'status', 'pending', 'run_at', when, 'last_error', '')
make_pending(P, id, member, {queue, okey, ekey, biz}, when, (ever ~= '') and tonumber(ever) or 0)
if biz ~= '' then
  redis.call('ZADD', P..'biz:'..biz, t, member)
  redis.call('ZREMRANGEBYSCORE', P..'biz:'..biz, 0, t - dttl * 1000)
end
return id
"""

_CLAIM = _LUA_COMMON + """
local P, worker, lease_s, limit, cap, per_queue = ARGV[1], ARGV[2], tonumber(ARGV[3]), tonumber(ARGV[4]), ARGV[5], tonumber(ARGV[6])
local t = now_ms()
local cands = {}
for qi = 7, #ARGV do
  local q = ARGV[qi]
  local ms = redis.call('ZRANGEBYSCORE', P..'pending:'..q, '-inf', t, 'WITHSCORES', 'LIMIT', 0, per_queue)
  for i = 1, #ms, 2 do
    local member = ms[i]
    local h = redis.call('HMGET', P..'job:'..tonumber(member), 'business_id', 'ordering_key')
    cands[#cands + 1] = {m = member, q = q, score = tonumber(ms[i + 1]), biz = h[1] or '', okey = h[2] or ''}
  end
end
table.sort(cands, function(a, b) if a.score ~= b.score then return a.score < b.score end return a.m < b.m end)
-- fairness: every tenant's oldest job before anyone's second one
local seen, ranked = {}, {}
for i, c in ipairs(cands) do
  seen[c.biz] = (seen[c.biz] or 0) + 1
  c.rank = seen[c.biz]; c.idx = i
  ranked[#ranked + 1] = c
end
table.sort(ranked, function(a, b) if a.rank ~= b.rank then return a.rank < b.rank end return a.idx < b.idx end)
local out = {}
for _, c in ipairs(ranked) do
  if #out >= limit then break end
  local ok = true
  if c.okey ~= '' then
    if redis.call('EXISTS', P..'okeyrun:'..c.q..':'..c.okey) == 1 then ok = false
    else
      local hd = redis.call('ZRANGE', P..'okey:'..c.q..':'..c.okey, 0, 0)
      if hd[1] ~= c.m then ok = false end
    end
  end
  if ok and cap ~= '' and c.biz ~= '' then
    local n = tonumber(redis.call('HGET', P..'trun:'..c.q, c.biz) or '0')
    if n >= tonumber(cap) then ok = false end
  end
  if ok then
    local id = tonumber(c.m)
    local h = head(P, id)
    unindex(P, id, c.m, h)
    redis.call('HINCRBY', P..'job:'..id, 'attempts', 1)
    redis.call('HSET', P..'job:'..id, 'status', 'running', 'locked_by', worker, 'lease_until', t + lease_s * 1000)
    redis.call('ZADD', P..'leases', t + lease_s * 1000, c.m)
    redis.call('SADD', P..'running:'..c.q, c.m)
    if c.okey ~= '' then redis.call('SET', P..'okeyrun:'..c.q..':'..c.okey, tostring(id)) end
    if c.biz ~= '' then redis.call('HINCRBY', P..'trun:'..c.q, c.biz, 1) end
    out[#out + 1] = id
  end
end
return out
"""

# one script for every way a running job leaves the running state
_RELEASE = _LUA_COMMON + """
local P, id, action, run_at, err = ARGV[1], tonumber(ARGV[2]), ARGV[3], tonumber(ARGV[4]), ARGV[5]
local member = string.format('%016d', id)
local jk = P..'job:'..id
if redis.call('EXISTS', jk) == 0 then return 'missing' end
local h = head(P, id)
local status = redis.call('HGET', jk, 'status')
if status ~= 'running' then return 'not-running' end
local t = now_ms()
release_running(P, id, member, h)
redis.call('HSET', jk, 'locked_by', '')
redis.call('HDEL', jk, 'lease_until')
if action == 'complete' then
  redis.call('HSET', jk, 'status', 'done', 'finished_at', t); redis.call('EXPIRE', jk, ARGV[6])
elseif action == 'supersede' then
  redis.call('HSET', jk, 'status', 'cancelled', 'finished_at', t); redis.call('EXPIRE', jk, ARGV[6])
elseif action == 'dead' then
  redis.call('HSET', jk, 'status', 'dead', 'last_error', err, 'finished_at', t)
  redis.call('ZADD', P..'dead:'..h[1], t, member)
elseif action == 'retry' then
  redis.call('HSET', jk, 'status', 'pending', 'last_error', err, 'run_at', run_at)
  make_pending(P, id, member, h, run_at, tonumber(h[5] ~= '' and h[5] or '0') or 0)
elseif action == 'defer' then
  redis.call('HINCRBY', jk, 'attempts', -1)
  redis.call('HSET', jk, 'status', 'pending', 'run_at', run_at)
  make_pending(P, id, member, h, run_at, tonumber(h[5] ~= '' and h[5] or '0') or 0)
end
return 'ok'
"""

_REAP = _LUA_COMMON + """
local P = ARGV[1]
local t = now_ms()
local expired = redis.call('ZRANGEBYSCORE', P..'leases', '-inf', t)
local n = 0
for _, member in ipairs(expired) do
  local id = tonumber(member)
  local jk = P..'job:'..id
  if redis.call('EXISTS', jk) == 1 and redis.call('HGET', jk, 'status') == 'running' then
    local h = head(P, id)
    release_running(P, id, member, h)
    local attempts, max = tonumber(redis.call('HGET', jk, 'attempts')), tonumber(redis.call('HGET', jk, 'max_attempts'))
    redis.call('HSET', jk, 'last_error', 'lease expired (worker died?)', 'locked_by', '')
    redis.call('HDEL', jk, 'lease_until')
    if attempts >= max then
      redis.call('HSET', jk, 'status', 'dead', 'finished_at', t)
      redis.call('ZADD', P..'dead:'..h[1], t, member)
    else
      redis.call('HSET', jk, 'status', 'pending', 'run_at', t)
      make_pending(P, id, member, h, t, tonumber(h[5] ~= '' and h[5] or '0') or 0)
    end
    n = n + 1
  else
    redis.call('ZREM', P..'leases', member)
  end
end
return n
"""

_CANCEL_SUPERSEDED = _LUA_COMMON + """
local P, ekey, below = ARGV[1], ARGV[2], ARGV[3]
local t = now_ms()
local members = redis.call('ZRANGEBYSCORE', P..'ent:'..ekey, '-inf', '('..below)
local n = 0
for _, member in ipairs(members) do
  local id = tonumber(member)
  local jk = P..'job:'..id
  if redis.call('EXISTS', jk) == 1 and redis.call('HGET', jk, 'status') == 'pending' then
    unindex(P, id, member, head(P, id))
    redis.call('HSET', jk, 'status', 'cancelled', 'finished_at', t); redis.call('EXPIRE', jk, ARGV[4])
    n = n + 1
  else
    redis.call('ZREM', P..'ent:'..ekey, member)
  end
end
return n
"""

_REPLAY = _LUA_COMMON + """
local P, q, id = ARGV[1], ARGV[2], tonumber(ARGV[3])
local member = string.format('%016d', id)
if redis.call('ZREM', P..'dead:'..q, member) == 0 then return 0 end
local jk = P..'job:'..id
local h = head(P, id)
local t = now_ms()
redis.call('HSET', jk, 'status', 'pending', 'attempts', 0, 'run_at', t, 'last_error', '')
redis.call('HDEL', jk, 'finished_at')
make_pending(P, id, member, h, t, tonumber(h[5] ~= '' and h[5] or '0') or 0)
return 1
"""

_PURGE = _LUA_COMMON + """
local P, biz = ARGV[1], ARGV[2]
local members = redis.call('ZRANGE', P..'biz:'..biz, 0, -1)
local n = 0
for _, member in ipairs(members) do
  local id = tonumber(member)
  local jk = P..'job:'..id
  if redis.call('EXISTS', jk) == 1 then
    local h = head(P, id)
    unindex(P, id, member, h)
    redis.call('ZREM', P..'dead:'..h[1], member)
    if redis.call('HGET', jk, 'status') == 'running' then release_running(P, id, member, h) end
    local dd = redis.call('HGET', jk, 'dedupe_key')
    if dd and dd ~= '' then redis.call('DEL', P..'dd:'..h[1]..':'..dd) end
    redis.call('DEL', jk)
    n = n + 1
  end
end
redis.call('DEL', P..'biz:'..biz)
return n
"""


def _ms(dt: datetime | None) -> str:
    return "" if dt is None else str(int(dt.timestamp() * 1000))


class RedisQueue:
    def __init__(self, url: str, *, prefix: str = "{salesai:q}:", backoff_base: float = 2.0,
                 client: aioredis.Redis | None = None):
        self.r: aioredis.Redis = client or aioredis.from_url(url, decode_responses=True)
        self.p = prefix
        self.backoff_base = backoff_base
        self._publish = self.r.register_script(_PUBLISH)
        self._claim = self.r.register_script(_CLAIM)
        self._release = self.r.register_script(_RELEASE)
        self._reap = self.r.register_script(_REAP)
        self._cancel = self.r.register_script(_CANCEL_SUPERSEDED)
        self._replay = self.r.register_script(_REPLAY)
        self._purge = self.r.register_script(_PURGE)

    async def close(self) -> None:
        await self.r.aclose()

    # ------------------------------------------------------------------ producer
    async def publish(self, spec: JobSpec) -> bool:
        res = await self._publish(keys=[], args=[
            self.p, spec.queue, spec.kind, json.dumps(spec.payload, default=str),
            str(spec.business_id or ""), spec.ordering_key or "", spec.entity_key or "",
            "" if spec.entity_version is None else str(spec.entity_version), spec.dedupe_key or "",
            _ms(spec.run_at), spec.max_attempts, _DEDUPE_TTL_S])
        return int(res) > 0

    # ------------------------------------------------------------------ consumer
    async def claim(self, queues: list[str], worker_id: str, *, lease_s: int = 120,
                    limit: int = 1, tenant_cap: int | None = None) -> list[Job]:
        ids = await self._claim(keys=[], args=[self.p, worker_id, lease_s, limit,
                                               "" if tenant_cap is None else tenant_cap, 400, *queues])
        jobs: list[Job] = []
        for i in ids:
            h = await self._hash(int(i))
            if h:
                jobs.append(self._to_job(int(i), h))
        return jobs

    async def _rel(self, job: Job, action: str, run_at_ms: int = 0, error: str = "") -> None:
        await self._release(keys=[], args=[self.p, int(job.id), action, run_at_ms, error[:2000], _DONE_TTL_S])

    async def _hash(self, job_id: int) -> dict[str, str]:
        raw = await self.r.hgetall(f"{self.p}job:{job_id}")
        return {str(k): str(v) for k, v in raw.items()}

    async def _now_ms(self) -> int:
        sec, usec = await self.r.time()
        return int(sec) * 1000 + int(usec) // 1000

    async def complete(self, job: Job) -> None:
        await self._rel(job, "complete")

    async def fail(self, job: Job, error: str, *, retryable: bool = True) -> str:
        dead = (not retryable) or job.attempts >= job.spec.max_attempts
        if dead:
            await self._rel(job, "dead", error=error)
            return "dead"
        at = await self._now_ms() + int(backoff_s(job.attempts, base=self.backoff_base) * 1000)
        await self._rel(job, "retry", at, error)
        return "retry"

    async def defer(self, job: Job, delay_s: float) -> None:
        await self._rel(job, "defer", await self._now_ms() + int(delay_s * 1000))

    async def supersede(self, job: Job) -> None:
        await self._rel(job, "supersede")

    async def cancel_superseded(self, entity_key: str, below_version: int) -> int:
        return int(await self._cancel(keys=[], args=[self.p, entity_key, below_version, _DONE_TTL_S]))

    async def reap_expired(self) -> int:
        return int(await self._reap(keys=[], args=[self.p]))

    # ------------------------------------------------------------------ operations
    async def stats(self, queues: list[str]) -> list[QueueStats]:
        now = await self._now_ms()
        out: list[QueueStats] = []
        for q in queues:
            pipe = self.r.pipeline()
            pipe.zcard(f"{self.p}pending:{q}")
            pipe.zcount(f"{self.p}pending:{q}", "-inf", now)
            pipe.scard(f"{self.p}running:{q}")
            pipe.zcard(f"{self.p}dead:{q}")
            pipe.zrangebyscore(f"{self.p}pending:{q}", "-inf", now, start=0, num=1, withscores=True)
            pending, due, running, dead, oldest = await pipe.execute()
            age = max(0.0, (now - oldest[0][1]) / 1000) if oldest else 0.0
            out.append(QueueStats(q, int(pending), int(due), int(running), int(dead), age))
        return out

    async def dead_letters(self, queue: str, limit: int = 50) -> list[dict[str, Any]]:
        members = await self.r.zrevrange(f"{self.p}dead:{queue}", 0, limit - 1)
        rows: list[dict[str, Any]] = []
        for raw in members:
            m = str(raw)
            h = await self._hash(int(m))
            if not h:
                continue
            fin = h.get("finished_at")
            rows.append({
                "id": str(int(m)), "kind": h["kind"], "business_id": uuid.UUID(h["business_id"]) if h.get("business_id") else None,
                "payload": json.loads(h["payload"]), "attempts": int(h["attempts"]), "last_error": h.get("last_error") or None,
                "finished_at": datetime.fromtimestamp(int(fin) / 1000, UTC) if fin else None})
        return rows

    async def replay_dead(self, queue: str, job_id: str) -> bool:
        return bool(await self._replay(keys=[], args=[self.p, queue, int(job_id)]))

    # ------------------------------------------------------------------ lifecycle (tenant delete, retention)
    async def purge_business(self, business_id: uuid.UUID) -> int:
        """Hard delete: remove every job of a tenant, whatever its state (DPDP erasure)."""
        return int(await self._purge(keys=[], args=[self.p, str(business_id)]))

    async def purge_finished(self, older_than: timedelta) -> int:
        """Finished jobs expire by TTL (`_DONE_TTL_S`), so there is nothing to sweep."""
        return 0

    @staticmethod
    def _to_job(i: int, h: dict[str, str]) -> Job:
        spec = JobSpec(
            queue=h["queue"], kind=h["kind"], payload=json.loads(h["payload"]),
            business_id=uuid.UUID(h["business_id"]) if h.get("business_id") else None,
            ordering_key=h.get("ordering_key") or None,
            run_at=datetime.fromtimestamp(int(float(h["run_at"])) / 1000, UTC) if h.get("run_at") else None,
            entity_key=h.get("entity_key") or None,
            entity_version=int(h["entity_version"]) if h.get("entity_version") else None,
            dedupe_key=h.get("dedupe_key") or None, max_attempts=int(h["max_attempts"]))
        return Job(id=str(i), spec=spec, attempts=int(h["attempts"]))
